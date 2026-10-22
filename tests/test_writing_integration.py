from dataclasses import replace
from types import SimpleNamespace as NS
from unittest.mock import Mock
import time
import numpy as np
import pytest
from visionguard.air_writing import SlideContext
from visionguard.config import Settings
from visionguard.gestures import Detection, Gesture as G, GestureStabilizer
from visionguard.office import OfficeWorker, Target, GestureEvent
from visionguard.security import Observation


def powerpoint():
    presentation = NS(FullName="fixture.pptx", PageSetup=NS(SlideWidth=1600, SlideHeight=900), Shapes=Mock())
    slide = NS(SlideID=256, Parent=presentation, Shapes=Mock(), Delete=Mock())
    view = NS(Slide=slide, State=1, Next=Mock(), Previous=Mock(), Exit=Mock(), EraseDrawing=Mock())
    window = NS(HWND=123, Presentation=presentation, View=view)
    return NS(SlideShowWindows=NS(Count=1, Item=lambda _: window)), window


def office():
    worker = OfficeWorker(get_foreground=lambda: (123, "screenClass"), get_slide_rect=lambda hwnd: (-1600, 0, 1600, 1000))
    worker.details = {"PowerPoint_running": True}
    worker.target = Target("PowerPoint", 123, "Slideshow ready", "slideshow", 123)
    return worker


def test_office_observes_slide_identity_and_geometry_without_modifying_content():
    w = office()
    app, win = powerpoint()
    w.refresh_slides(lambda _: app, 100)
    first = w.current_slide(100)
    assert first.rect == (-1600, 50, 1600, 900)
    win.View.Slide.SlideID = 257
    w.refresh_slides(lambda _: app, 100.1)
    second = w.current_slide(100.1)
    assert first.session == second.session and first.key != second.key
    assert not win.View.Next.called and not win.View.Previous.called
    assert not win.View.EraseDrawing.called and not win.View.Slide.Delete.called
    assert not win.View.Slide.Shapes.mock_calls and not win.Presentation.Shapes.mock_calls


def test_closed_and_reopened_show_gets_new_session_and_com_errors_fail_closed():
    w = office()
    app, _ = powerpoint()
    w.refresh_slides(lambda _: app, 100)
    first = w.current_slide(100)
    w.refresh_slides(Mock(side_effect=RuntimeError("busy")), 100.1)
    assert w.current_slide(100.1) is None and not w.slide_snapshot()[2]
    app.SlideShowWindows.Count = 0
    w.refresh_slides(lambda _: app, 100.2)
    assert w.slide_snapshot()[0] == () and w.slide_snapshot()[2]
    app.SlideShowWindows.Count = 1
    w.refresh_slides(lambda _: app, 100.3)
    assert w.current_slide(100.3).session != first.session


@pytest.mark.parametrize("state", [3, 4, 5])
def test_black_white_and_finished_show_dont_receive_ink(state):
    w = office()
    app, win = powerpoint()
    win.View.State = state
    w.refresh_slides(lambda _: app, 100)
    assert w.current_slide(100) is None
    assert len(w.slide_snapshot()[0]) == 1  # Preserve stored ink while blacked out.


def test_stale_metadata_and_focus_change_cannot_select_slide():
    w = office()
    app, _ = powerpoint()
    w.refresh_slides(lambda _: app, 100)
    assert w.current_slide(100.3) is None
    w.get_foreground = lambda: (999, "Notepad")
    assert w.current_slide(100.1) is None


@pytest.fixture
def live_worker(qt_app, database, embedding, monkeypatch):
    import visionguard.runtime as runtime
    clock = NS(now=100.)
    monkeypatch.setattr(runtime.time, "monotonic", lambda: clock.now)
    monkeypatch.setattr(runtime, "session_locked", lambda: False)
    database.enroll(np.tile(embedding, (6, 1)))
    worker = runtime.VisionWorker(Settings(camera_consent=True), database)
    worker.office.get_foreground = lambda: (123, "screenClass")
    worker.office.target = Target("PowerPoint", 123, "Slideshow ready", "slideshow", 123)
    worker.office.submit = Mock()
    worker.lock_manager.lock = Mock()
    stabilizer = GestureStabilizer()

    def frame(gesture=None, point=(.5,.5), matched=True):
        clock.now += .05
        worker.tick_policy(Observation(authorized=matched, kind="matched" if matched else "unknown"),
                           clock.now, face_count=1)
        worker.office._slides = (SlideContext("show", 1, 123, (0,0,1600,900), clock.now),)
        worker.office._slides_at, worker.office._slides_known = clock.now, True
        marks = [[(int(point[0]*640), int(point[1]*480))]*21] if gesture else []
        worker.dispatch_gesture([Detection(gesture, .99, "Right")] if gesture else [], stabilizer,
                                 clock.now, marks, (640,480), clock.now)

    def hold(gesture, n=15, **kwargs):
        for i in range(n):
            frame(gesture, **kwargs)

    for _ in range(4):
        frame()
    return NS(worker=worker, clock=clock, frame=frame, hold=hold, stabilizer=stabilizer)


def test_runtime_three_index_clear_never_sends_document_mutating_COM(live_worker):
    r = live_worker
    r.hold(G.TWO_FINGERS, 3)
    r.hold(G.THREE_FINGERS)
    assert r.worker.air.mode
    r.hold(G.TWO_FINGERS, 3)
    r.frame(G.INDEX, (.3,.4))
    r.frame(G.INDEX, (.4,.5))
    r.frame(G.INDEX, (.5,.6))
    assert r.worker.air.snapshot()["strokes"]
    r.hold(G.LEFT_L)
    r.hold(G.RIGHT_L)
    r.worker.office.submit.assert_not_called()
    r.hold(G.TWO_FINGERS, 3)
    r.hold(G.OPEN_PALM)
    assert not r.worker.air.ink and r.worker.air.mode
    r.worker.office.submit.assert_not_called()


def test_runtime_preserves_legacy_navigation_when_no_ink_metadata(live_worker):
    r = live_worker
    r.worker.office._slides_known = False
    for gesture in [G.OPEN_PALM]*4 + [G.RIGHT_L]*12:
        r.clock.now += .05
        r.worker.tick_policy(Observation(authorized=True, kind="matched"), r.clock.now, face_count=1)
        r.worker.dispatch_gesture([Detection(gesture, .99, "Right")], r.stabilizer, r.clock.now)
    r.worker.office.submit.assert_called_once()
    assert r.worker.office.submit.call_args.args[0].command == "next"


@pytest.mark.parametrize("kind,camera,faces", [("absent", True, 0), ("quality", True, 1),
    ("unknown", True, 1), ("camera_error", False, 0), ("multiple", True, 2)])
def test_face_loss_including_grace_or_camera_failure_immediately_stops_writing(live_worker, kind, camera, faces):
    r = live_worker
    r.hold(G.TWO_FINGERS, 3)
    r.hold(G.THREE_FINGERS)
    r.hold(G.TWO_FINGERS, 3)
    r.hold(G.INDEX, 3)
    before = r.worker.air.snapshot()["strokes"]
    r.clock.now += .05
    r.worker.tick_policy(Observation(camera_ok=camera, kind=kind), r.clock.now, face_count=faces,
                         camera="Connected" if camera else "Disconnected")
    assert not r.worker.writing_permit()
    r.worker.writing_housekeeping()
    assert r.worker.air.stroke is None and not r.worker.air.mode
    assert r.worker.air.snapshot()["strokes"] == before


def test_air_writing_does_not_reset_windows_absence_timer(live_worker):
    r = live_worker
    r.worker.state.configure(replace(r.worker.settings, auto_lock=True))
    r.hold(G.TWO_FINGERS, 3)
    r.hold(G.THREE_FINGERS)
    for i in range(8):
        r.clock.now += 1
        r.worker.tick_policy(Observation(kind="absent"), r.clock.now, face_count=0)
        r.worker.writing_housekeeping()
    r.worker.lock_manager.lock.assert_called_once()


def test_clear_rechecks_authorization_and_slide_at_worker_consumption(live_worker):
    r = live_worker
    r.hold(G.TWO_FINGERS, 3)
    r.hold(G.THREE_FINGERS)
    r.hold(G.TWO_FINGERS, 3)
    r.hold(G.INDEX, 3)
    before = r.worker.air.snapshot()["strokes"]
    assert r.worker.request_ink_clear()
    r.worker.invalidate("User disabled")
    r.worker._commands(None, r.stabilizer)
    assert r.worker.air.snapshot()["strokes"] == before
    assert not r.worker.request_ink_clear(True)


def test_clear_gui_allowed_for_verified_user_while_settings_has_focus(live_worker):
    r = live_worker
    r.hold(G.TWO_FINGERS, 3)
    r.hold(G.THREE_FINGERS)
    r.hold(G.TWO_FINGERS, 3)
    r.hold(G.INDEX, 3)
    r.worker.office.get_foreground = lambda: (999, "QtWindow")
    assert r.worker.request_ink_clear()
    r.worker._commands(None, r.stabilizer)
    assert not r.worker.air.ink


def test_queued_navigation_rejected_if_writing_mode_activated(live_worker):
    r = live_worker
    r.worker.air.mode = True
    target = r.worker.office.target
    event = GestureEvent("RIGHT_L", "Right", .99, r.clock.now, True, True,
                         r.worker.state.snapshot().auth_epoch, target, "next")
    getter = Mock()
    assert "suppressed" in r.worker.office.dispatch(event, getter, r.clock.now)
    getter.assert_not_called()


def test_calibration_uses_fresh_index_sample_and_blocks_all_office_commands(live_worker):
    r = live_worker
    r.worker.calibration_preview = True
    r.hold(G.RIGHT_L)
    r.worker.office.submit.assert_not_called()
    assert r.worker.calibration_sample() is None
    r.frame(G.INDEX, (.25,.75))
    assert r.worker.calibration_sample() == (.25,.75)
    r.clock.now += .3
    assert r.worker.calibration_sample() is None
