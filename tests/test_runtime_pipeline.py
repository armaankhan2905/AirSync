"""Connected worker tests with explicitly mocked hardware and model outputs."""
from types import SimpleNamespace as NS
from unittest.mock import Mock
import numpy as np
import pytest
from visionguard.authentication import Face
from visionguard.config import Settings
from visionguard.security import State
from visionguard.face_worker import FaceResult


def test_live_softness_and_short_dropout_can_finish_challenge_but_unknown_still_revokes(
        qt_app, database, embedding, monkeypatch):
    import visionguard.runtime as runtime
    from visionguard.authentication import FaceMatcher
    database.enroll(np.tile(embedding, (6, 1)))
    worker = runtime.VisionWorker(Settings(camera_consent=True), database)
    worker.enrolled = True
    matcher = FaceMatcher(database.embeddings())
    clock = NS(now=100.)
    monkeypatch.setattr(runtime.time, "monotonic", lambda: clock.now)
    first, second, _ = worker.challenge.sequence
    poses = [first * .3, first * .3, None, second * .3, second * .3, 0., 0., 0., 0.]
    for sequence, yaw in enumerate(poses):
        clock.now = 100 + sequence * .25
        face = Face((100, 100, 145, 185), embedding, -(yaw or 0), False, "Hold still; face is too blurred",
                    verification_quality=yaw is not None)
        worker.process_faces(FaceResult(sequence, clock.now, clock.now, [face], 10), matcher)
        if yaw is None:
            assert worker.challenge.step == 1
            assert not worker.decision.controls_allowed
    assert worker.decision.state == State.VERIFIED
    clock.now += .25
    unknown = Face((100, 100, 145, 185), -embedding, 0, False, "Hold still; face is too blurred",
                   verification_quality=True)
    worker.process_faces(FaceResult(10, clock.now, clock.now, [unknown], 10), matcher)
    assert worker.decision.state == State.UNKNOWN_PERSON
    assert not worker.decision.controls_allowed
    assert database.embeddings().shape == (6, 128)


def test_multiple_faces_reset_incomplete_liveness_even_with_dropout_grace(qt_app, database, embedding, monkeypatch):
    import visionguard.runtime as runtime
    from visionguard.authentication import FaceMatcher
    database.enroll(np.tile(embedding, (6, 1)))
    worker = runtime.VisionWorker(Settings(), database)
    worker.enrolled = True
    worker.challenge.step, worker.challenge.started = 1, 99.
    monkeypatch.setattr(runtime.time, "monotonic", lambda: 100.)
    faces = [Face((10, 10, 120, 160), embedding, 0, True, ""),
             Face((200, 10, 120, 160), -embedding, 0, True, "")]
    worker.process_faces(FaceResult(1, 100., 100., faces, 10), FaceMatcher(database.embeddings()))
    assert worker.challenge.step == 0
    assert not worker.decision.controls_allowed


@pytest.fixture(autouse=True)
def deterministic_face_mailbox(monkeypatch):
    """Thread scheduling has separate tests; these test orchestrator decisions."""
    import visionguard.runtime as runtime
    class Mailbox:
        def __init__(self, factory):
            self.factory, self.sequence, self.result = factory, 0, None
        def start(self):
            self.engine = self.factory()
        def submit(self, frame, captured):
            self.sequence += 1
            self.result = FaceResult(self.sequence, captured, captured, self.engine.detect(frame), 10)
        def latest(self):
            return self.result
        def stop(self):
            pass
    monkeypatch.setattr(runtime, "FaceInferenceWorker", Mailbox)


@pytest.mark.parametrize("mode", ["owner_then_unknown", "owner_only", "multiple_faces"])
def test_connected_face_presence_and_office_gate(qt_app, database, embedding, monkeypatch, mode):
    import visionguard.runtime as runtime
    database.enroll(np.tile(embedding, (6, 1)))
    worker = runtime.VisionWorker(Settings(auto_lock=True, camera_consent=True), database, simulation=True)
    worker.lock_manager.lock = Mock(return_value=True)
    worker.office.start = Mock()
    worker.office.stop = Mock()
    worker.office.submit = Mock()
    clock = NS(now=100., frames=0)
    frame = np.random.default_rng(12).integers(50, 200, (480, 640, 3), dtype=np.uint8)

    class FakeCamera:
        index = 0
        def start(self):
            pass
        def stop(self):
            pass
        def latest(self):
            clock.now += .25
            clock.frames += 1
            return frame, clock.now, ""

    class FakeFaces:
        def __init__(self, path):
            pass
        def detect(self, frame):
            owner = Face((100, 100, 150, 150), embedding, 0., True, "")
            unknown = Face((300, 100, 150, 150), -embedding, 0., True, "")
            if mode == "multiple_faces" and clock.frames >= 6:
                return [owner, unknown]
            return [owner] if mode in {"owner_only", "multiple_faces"} or clock.frames < 6 else [unknown]

    class FakeHands:
        def __init__(self, path):
            pass
        def detect(self, *args):
            if clock.frames >= 36:
                clock.decision = worker.decision
                worker.stop_event.set()
            return [], []
        def close(self):
            pass

    monkeypatch.setattr(runtime, "CameraManager", lambda index: FakeCamera())
    monkeypatch.setattr(runtime, "FaceEngine", FakeFaces)
    monkeypatch.setattr(runtime, "HandTracker", FakeHands)
    monkeypatch.setattr(runtime, "verify_models", lambda path: None)
    monkeypatch.setattr(runtime.time, "monotonic", lambda: clock.now)
    monkeypatch.setattr(runtime, "session_locked", lambda: False)
    worker.last_progress = clock.now
    # Deliberately mock challenge for orchestration isolation. Real challenge
    # sequencing and spoof-stationarity rejection have independent unit tests.
    def mock_challenge(yaw, now, identity_ok=True):
        if yaw is not None and identity_ok:
            worker.challenge.valid_until = now + 60
            return True
        return False
    worker.challenge.update = mock_challenge
    worker.challenge.valid_until = 1000
    worker.run()
    if mode == "owner_then_unknown":
        worker.lock_manager.lock.assert_called_once()
        assert clock.decision.state == State.LOCK_PENDING
    else:
        worker.lock_manager.lock.assert_not_called()
        assert clock.decision.state == State.VERIFIED
    # No hand outputs: no Office commands can be manufactured by orchestration.
    worker.office.submit.assert_not_called()
    assert not worker.permit()  # cleanup revokes permission


@pytest.mark.parametrize("slideshow", [False, True])
def test_verified_owner_neutral_and_L_gestures_require_ready_slideshow(
        qt_app, database, embedding, monkeypatch, slideshow):
    import visionguard.runtime as runtime
    from visionguard.gestures import Detection, Gesture
    from visionguard.office import Target
    database.enroll(np.tile(embedding, (6, 1)))
    worker = runtime.VisionWorker(Settings(camera_consent=True), database)
    worker.office.start = Mock()
    worker.office.stop = Mock()
    worker.office.submit = Mock()
    worker.office.target = Target("PowerPoint", 123, "Slideshow ready" if slideshow else "Press F5")
    worker.challenge.step, worker.challenge.valid_until = 3, 200
    worker.challenge.update = lambda yaw, now, identity_ok=True: yaw is not None and identity_ok
    clock = NS(now=100., frames=0)
    frame = np.zeros((480, 640, 3), dtype=np.uint8)

    class Camera:
        index = 0
        start = stop = lambda self: None
        def latest(self):
            clock.now += .25
            clock.frames += 1
            return frame, clock.now, ""

    class Faces:
        def detect(self, frame):
            return [Face((100, 100, 150, 150), embedding, 0., True, "")]

    class Hands:
        close = lambda self: None
        def detect(self, *args):
            n = clock.frames
            gesture = Gesture.NEUTRAL if n <= 7 or 13 <= n <= 17 else Gesture.RIGHT_L if n <= 12 else Gesture.LEFT_L
            if n >= 24:
                worker.stop_event.set()
            return [Detection(gesture, .99, "Left" if gesture == Gesture.LEFT_L else "Right")], []

    monkeypatch.setattr(runtime, "CameraManager", lambda index: Camera())
    monkeypatch.setattr(runtime, "FaceEngine", lambda path: Faces())
    monkeypatch.setattr(runtime, "HandTracker", lambda path: Hands())
    monkeypatch.setattr(runtime, "verify_models", lambda path: None)
    monkeypatch.setattr(runtime.time, "monotonic", lambda: clock.now)
    monkeypatch.setattr(runtime, "session_locked", lambda: False)
    worker.last_progress = clock.now
    worker.run()
    assert not worker.health_error
    gestures = [call.args[0].gesture for call in worker.office.submit.call_args_list]
    assert gestures == ([Gesture.RIGHT_L, Gesture.LEFT_L] if slideshow else [])
    if slideshow:
        events = [call.args[0] for call in worker.office.submit.call_args_list]
        assert [(e.hand, e.command) for e in events] == [("Right", "next"), ("Left", "previous")]
        assert all(e.stable and e.authorized and e.confidence == .99 for e in events)


def test_quality_failure_not_unknown_and_recognition_error_recovers(qt_app, database, embedding, monkeypatch):
    import visionguard.runtime as runtime
    from visionguard.authentication import FaceMatcher
    database.enroll(np.tile(embedding, (6, 1)))
    worker = runtime.VisionWorker(Settings(camera_consent=True), database)
    worker.enrolled = True
    worker.challenge.step, worker.challenge.valid_until = 3, 100
    matcher = FaceMatcher(database.embeddings())
    clock = NS(now=10.)
    monkeypatch.setattr(runtime.time, "monotonic", lambda: clock.now)
    owner = lambda: Face((0, 0, 120, 120), embedding, 0, True, "Good sample")
    for n in range(3):
        clock.now = 10 + .2 * n
        worker.process_faces(FaceResult(n, clock.now, clock.now, [owner()], 20), matcher)
    assert worker.decision.controls_allowed
    clock.now = 10.6
    blurred = Face((0, 0, 120, 120), embedding, 0, False, "Hold still; face is too blurred")
    worker.process_faces(FaceResult(4, clock.now, clock.now, [blurred], 20), matcher)
    snap = worker.state.snapshot()
    assert snap.face_status == "quality" and snap.decision.in_grace
    clock.now = 10.8
    worker.process_faces(FaceResult(5, clock.now, clock.now, [], 20, "Face inference failed: ValueError"), matcher)
    assert worker.decision.state == State.RECOGNITION_ERROR
    assert not worker.state.permit(clock.now)
    worker.challenge.step, worker.challenge.valid_until = 3, 100
    for n in range(3):
        clock.now = 11 + .2 * n
        worker.process_faces(FaceResult(6 + n, clock.now, clock.now, [owner()], 20), matcher)
    assert worker.decision.controls_allowed
