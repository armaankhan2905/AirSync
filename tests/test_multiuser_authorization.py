"""Automatic recognition and independent Office/workstation authorization."""
from dataclasses import replace
from types import SimpleNamespace as NS
from unittest.mock import Mock
import time
import numpy as np
import pytest
from visionguard.authentication import Face, FaceMatcher
from visionguard.config import Settings
from visionguard.face_worker import FaceResult
from visionguard.gestures import GestureStabilizer
from visionguard.office import GestureEvent, Target
from visionguard.recognition import RecognitionDirectory, public_user
from visionguard.security import Observation, State
from visionguard.state import StateManager
from visionguard.users import Permissions


def vector(index):
    return np.eye(128, dtype=np.float32)[index]


@pytest.fixture
def registered(database):
    database.set_passphrase("multi-user-test-administrator")
    token = database.users.authenticate_admin("multi-user-test-administrator")
    owner = database.users.register("Owner", np.tile(vector(0), (6, 1)), token)
    guest = database.users.register("Presenter", np.tile(vector(1), (6, 1)), token,
        permissions=Permissions(can_control_word=False))
    return NS(token=token, owner=owner, guest=guest)


def face(index, yaw=0., box=(100, 100, 145, 185)):
    return Face(box, vector(index), yaw, True, "Good sample")


def verify_worker(worker, index, clock):
    """A real randomized liveness sequence using mocked face inference only."""
    matcher = FaceMatcher([])
    clock.now += .25
    first = face(index)
    worker.process_faces(FaceResult(1, clock.now, clock.now, [first], 10), matcher)
    track = worker.directory.tracks[first.track_id]
    sequence = list(track["challenge"].sequence)
    for yaw in [t * .3 for t in sequence for _ in range(2)] + [0., 0.]:
        clock.now += .25
        worker.process_faces(FaceResult(1, clock.now, clock.now, [face(index, -yaw)], 10), matcher)
    return worker.state.snapshot()


def test_registry_auto_matching_after_restart_and_no_match_for_unknown(database, registered):
    before = RecognitionDirectory(database.users, Settings())
    assert before.identify(vector(1))[0].user_id == registered.guest.user_id
    after = RecognitionDirectory(database.users, Settings())
    assert after.identify(vector(0))[0].user_id == registered.owner.user_id
    assert after.identify(vector(2))[0] is None


def test_uncertain_and_ambiguous_matches_do_not_choose_a_user(database, registered):
    directory = RecognitionDirectory(database.users, Settings())
    query = .52 * vector(0) + np.sqrt(1 - .52 ** 2) * vector(2)
    assert directory.identify(query)[0] is None
    assert directory.identify(query)[2] == "uncertain"
    ambiguous = (vector(0) + vector(1)) / np.sqrt(2)
    assert directory.identify(ambiguous)[0] is None
    assert directory.identify(ambiguous)[2] == "uncertain"


def test_hysteresis_applies_only_to_the_same_verified_identity(database, registered):
    directory = RecognitionDirectory(database.users, Settings())
    query = .51 * vector(0) + np.sqrt(1 - .51 ** 2) * vector(2)
    assert directory.identify(query)[0] is None
    assert directory.identify(query, registered.owner.user_id)[0].user_id == registered.owner.user_id
    assert directory.identify(query, registered.guest.user_id)[0] is None


def test_spatial_tracks_survive_face_order_changes_without_sharing_liveness(database, registered):
    directory = RecognitionDirectory(database.users, Settings())
    first = [face(0, box=(20, 20, 120, 160)), face(1, box=(300, 20, 120, 160))]
    directory.observe(first, 10.)
    tracks = {f.user_id: f.track_id for f in first}
    second = [face(1, box=(302, 20, 120, 160)), face(0, box=(22, 20, 120, 160))]
    directory.observe(second, 10.2)
    assert {f.user_id: f.track_id for f in second} == tracks
    assert all(not f.live for f in second)
    assert all(t["challenge"].step == 0 for t in directory.tracks.values())


def test_identity_change_on_same_track_cannot_reuse_completed_liveness(database, registered):
    directory = RecognitionDirectory(database.users, Settings())
    first = face(0)
    directory.observe([first], 10.)
    challenge = directory.tracks[first.track_id]["challenge"]
    challenge.step, challenge.valid_until = 3, 100.
    replacement = face(1)
    directory.observe([replacement], 10.2)
    assert replacement.track_id == first.track_id
    assert not replacement.live
    assert challenge.step == 0 and challenge.valid_until == 0


def test_runtime_automatically_verifies_registered_owner_and_rejects_unknown(qt_app, database, registered, monkeypatch):
    from visionguard.runtime import VisionWorker
    clock = NS(now=100.)
    monkeypatch.setattr("visionguard.runtime.time.monotonic", lambda: clock.now)
    monkeypatch.setattr("visionguard.runtime.session_locked", lambda: False)
    worker = VisionWorker(Settings(), database)
    snapshot = verify_worker(worker, 0, clock)
    assert snapshot.identity_decision.controls_allowed
    assert snapshot.identity_user["user_name"] == "Owner"
    assert snapshot.decision.controls_allowed
    assert worker.permit("PowerPoint") and worker.permit("Word")
    assert database.users.get(registered.owner.user_id).last_recognized_at is not None
    clock.now += .25
    worker.process_faces(FaceResult(20, clock.now, clock.now, [face(2)], 10), FaceMatcher([]))
    assert not worker.permit("PowerPoint")
    assert worker.state.snapshot().identity_decision.state == State.UNKNOWN_PERSON


def test_stationary_matching_face_does_not_verify_without_liveness(qt_app, database, registered, monkeypatch):
    from visionguard.runtime import VisionWorker
    clock = NS(now=100.)
    monkeypatch.setattr("visionguard.runtime.time.monotonic", lambda: clock.now)
    worker = VisionWorker(Settings(), database)
    for i in range(30):
        clock.now += .25
        worker.process_faces(FaceResult(i, clock.now, clock.now, [face(0)], 10), FaceMatcher([]))
    assert not worker.state.snapshot().identity_decision.controls_allowed


def test_presenter_gets_office_access_but_does_not_prevent_workstation_lock(qt_app, database, registered, monkeypatch):
    from visionguard.runtime import VisionWorker
    clock = NS(now=100.)
    monkeypatch.setattr("visionguard.runtime.time.monotonic", lambda: clock.now)
    monkeypatch.setattr("visionguard.runtime.session_locked", lambda: False)
    worker = VisionWorker(Settings(auto_lock=True, absence_timeout=5.), database)
    worker.lock_manager.lock = Mock(return_value=True)
    snapshot = verify_worker(worker, 1, clock)
    assert snapshot.identity_decision.controls_allowed
    assert not snapshot.decision.controls_allowed
    assert worker.permit("PowerPoint") and not worker.permit("Word")
    for i in range(20):
        clock.now += .25
        worker.process_faces(FaceResult(i, clock.now, clock.now, [face(1)], 10), FaceMatcher([]))
    worker.lock_manager.lock.assert_called_once()
    assert worker.decision.state == State.LOCK_PENDING
    assert not worker.permit("PowerPoint")


def test_explicitly_trusted_user_can_satisfy_presence_policy(qt_app, database, registered, monkeypatch):
    from visionguard.runtime import VisionWorker
    database.users.update(registered.guest.user_id, registered.token,
        permissions=replace(registered.guest.permissions, trusted_presence=True))
    clock = NS(now=100.)
    monkeypatch.setattr("visionguard.runtime.time.monotonic", lambda: clock.now)
    worker = VisionWorker(Settings(auto_lock=True), database)
    snapshot = verify_worker(worker, 1, clock)
    assert snapshot.identity_decision.controls_allowed and snapshot.decision.controls_allowed


def test_multiple_people_retain_individual_matches_but_block_all_gestures(qt_app, database, registered, monkeypatch):
    from visionguard.runtime import VisionWorker
    clock = NS(now=100.)
    monkeypatch.setattr("visionguard.runtime.time.monotonic", lambda: clock.now)
    monkeypatch.setattr("visionguard.runtime.session_locked", lambda: False)
    worker = VisionWorker(Settings(), database)
    verify_worker(worker, 0, clock)
    clock.now += .25
    faces = [face(0), face(2, box=(300, 100, 120, 160))]
    worker.process_faces(FaceResult(30, clock.now, clock.now, faces, 10), FaceMatcher([]))
    assert faces[0].user_id == registered.owner.user_id and faces[1].user_id is None
    assert not worker.permit("PowerPoint")
    assert worker.view()["face_status"] == "multiple"


def test_strict_privacy_distinguishes_registered_guest_from_unknown_extra_face(qt_app, database, registered, monkeypatch):
    from visionguard.runtime import VisionWorker
    clock = NS(now=100.)
    monkeypatch.setattr("visionguard.runtime.time.monotonic", lambda: clock.now)
    worker = VisionWorker(Settings(strict_privacy=True), database)
    verify_worker(worker, 0, clock)
    clock.now += .25
    worker.process_faces(FaceResult(31, clock.now, clock.now, [face(0), face(1, box=(300, 100, 120, 160))], 10), FaceMatcher([]))
    assert worker.decision.controls_allowed  # The trusted owner is still present.
    assert not worker.state.permit(clock.now, "PowerPoint")  # Multiple people still block all hands.
    clock.now += .25
    worker.process_faces(FaceResult(32, clock.now, clock.now, [face(0), face(2, box=(300, 100, 120, 160))], 10), FaceMatcher([]))
    assert not worker.decision.controls_allowed


@pytest.mark.parametrize("mutation", ["disable", "delete", "permission"])
def test_backend_immediately_rejects_cached_identity_after_user_change(qt_app, database, registered, monkeypatch, mutation):
    from visionguard.runtime import VisionWorker
    clock = NS(now=100.)
    monkeypatch.setattr("visionguard.runtime.time.monotonic", lambda: clock.now)
    monkeypatch.setattr("visionguard.runtime.session_locked", lambda: False)
    worker = VisionWorker(Settings(), database)
    verify_worker(worker, 1, clock)
    assert worker.permit("PowerPoint")
    if mutation == "disable":
        database.users.update(registered.guest.user_id, registered.token, active=False)
    elif mutation == "delete":
        database.users.delete(registered.guest.user_id, registered.token)
    else:
        database.users.update(registered.guest.user_id, registered.token,
            permissions=replace(registered.guest.permissions, can_control_powerpoint=False))
    assert not worker.permit("PowerPoint")  # No GUI callback or new camera frame required.
    clock.now += .25
    worker.process_faces(FaceResult(40, clock.now - .1, clock.now, [face(1)], 10), FaceMatcher([]))
    assert not worker.permit("PowerPoint")


def test_old_face_result_after_directory_change_is_discarded(qt_app, database, registered, monkeypatch):
    from visionguard.runtime import VisionWorker
    clock = NS(now=100.)
    monkeypatch.setattr("visionguard.runtime.time.monotonic", lambda: clock.now)
    worker = VisionWorker(Settings(), database)
    verify_worker(worker, 1, clock)
    clock.now += .25
    database.users.update(registered.guest.user_id, registered.token, active=False)
    assert worker.process_faces(FaceResult(50, clock.now - .1, clock.now, [face(1)], 10), FaceMatcher([])) is None
    assert not worker.state.snapshot().identity_decision.controls_allowed


@pytest.mark.parametrize("app, expected", [("PowerPoint", True), ("Word", False)])
def test_office_dispatch_enforces_application_permission_in_backend(qt_app, database, registered, monkeypatch, app, expected):
    from visionguard.runtime import VisionWorker
    clock = NS(now=time.monotonic())
    monkeypatch.setattr("visionguard.runtime.time.monotonic", lambda: clock.now)
    monkeypatch.setattr("visionguard.runtime.session_locked", lambda: False)
    monkeypatch.setattr("visionguard.office.same_window", lambda a, b: a == b)
    worker = VisionWorker(Settings(), database)
    snapshot = verify_worker(worker, 1, clock)
    target = Target(app, 100, "Slideshow ready" if app == "PowerPoint" else "Word ready")
    worker.office.target = target
    worker.office.get_foreground = lambda: (100, "screenClass")
    view = NS(Slide=NS(SlideIndex=1), Next=Mock())
    powerpoint = NS(SlideShowWindows=NS(Count=1, Item=lambda i: NS(HWND=100, View=view,
        Presentation=NS(SlideShowSettings=NS(RangeType=1), Slides=NS(Count=2, Item=lambda i: NS(SlideShowTransition=NS(Hidden=0)))))))
    getter = Mock(return_value=powerpoint)
    event = GestureEvent("RIGHT_L", "Right", .99, clock.now, True, True, snapshot.auth_epoch, target, "next", registered.guest.user_id)
    result = worker.office.dispatch(event, getter, clock.now)
    if expected:
        assert result == "PowerPoint: next"
        view.Next.assert_called_once()
    else:
        assert "permission" in result
        getter.assert_not_called()


def test_authorized_word_execution_and_unregistered_rejection(qt_app, database, registered, monkeypatch):
    from visionguard.runtime import VisionWorker
    clock = NS(now=time.monotonic())
    monkeypatch.setattr("visionguard.runtime.time.monotonic", lambda: clock.now)
    monkeypatch.setattr("visionguard.runtime.session_locked", lambda: False)
    monkeypatch.setattr("visionguard.office.same_window", lambda a, b: a == b)
    worker = VisionWorker(Settings(), database)
    snapshot = verify_worker(worker, 0, clock)
    target = Target("Word", 100, "Word ready")
    worker.office.target = target
    worker.office.get_foreground = lambda: (100, "OpusApp")
    pane = NS(PageScroll=Mock())
    app = NS(Documents=NS(Count=1), ActiveWindow=NS(Hwnd=100, View=NS(Type=3), ActivePane=pane))
    getter = Mock(return_value=app)
    event = GestureEvent("RIGHT_L", "Right", .99, clock.now, True, True, snapshot.auth_epoch, target, "next", registered.owner.user_id)
    assert "Word: next" in worker.office.dispatch(event, getter, clock.now)
    pane.PageScroll.assert_called_once_with(Down=1)
    clock.now += .25
    worker.process_faces(FaceResult(60, clock.now, clock.now, [face(2)], 10), FaceMatcher([]))
    getter.reset_mock()
    assert "denied" in worker.office.dispatch(event, getter, clock.now)
    getter.assert_not_called()


def test_permission_revocation_during_com_lookup_blocks_the_actual_command(qt_app, database, registered, monkeypatch):
    from visionguard.runtime import VisionWorker
    clock = NS(now=time.monotonic())
    monkeypatch.setattr("visionguard.runtime.time.monotonic", lambda: clock.now)
    monkeypatch.setattr("visionguard.runtime.session_locked", lambda: False)
    monkeypatch.setattr("visionguard.office.same_window", lambda a, b: a == b)
    worker = VisionWorker(Settings(), database)
    snapshot = verify_worker(worker, 1, clock)
    target = Target("PowerPoint", 100, "Slideshow ready")
    worker.office.target = target
    worker.office.get_foreground = lambda: (100, "screenClass")
    view = NS(Slide=NS(SlideIndex=1), Next=Mock())
    app = NS(SlideShowWindows=NS(Count=1, Item=lambda i: NS(HWND=100, View=view)))
    def lookup(_):
        database.users.update(registered.guest.user_id, registered.token, active=False)
        return app
    event = GestureEvent("RIGHT_L", "Right", .99, clock.now, True, True, snapshot.auth_epoch, target, "next", registered.guest.user_id)
    assert "cancelled" in worker.office.dispatch(event, lookup, clock.now)
    view.Next.assert_not_called()


def test_registration_worker_requires_admin_and_rechecks_at_commit(qt_app, database, monkeypatch):
    from visionguard.runtime import VisionWorker
    database.set_passphrase("registration-worker-administrator")
    token = database.users.authenticate_admin("registration-worker-administrator")
    clock = NS(now=time.monotonic())
    monkeypatch.setattr("visionguard.runtime.time.monotonic", lambda: clock.now)
    worker = VisionWorker(Settings(), database)
    results = []
    worker.registration_result.connect(results.append)
    worker.command("register", {"token": "invalid", "name": "Intruder"})
    worker._commands(FaceMatcher([]), GestureStabilizer())
    assert worker.enrolling is None and not results[-1]["success"]
    worker.command("register", {"token": token, "name": "Owner"})
    worker._commands(FaceMatcher([]), GestureStabilizer())
    assert worker.enrolling is not None
    worker.enrolling.samples = list(np.tile(vector(0), (6, 1)))
    worker.enrolling.add = lambda *args: "READY"
    database.users._sessions[token] = (0., None)
    clock.now += .25
    worker.process_faces(FaceResult(70, clock.now, clock.now, [face(0)], 10), FaceMatcher([]))
    assert not database.users.has_users()
    assert not results[-1]["success"] and "expired" in results[-1]["message"]


def test_named_registration_is_saved_and_available_to_next_worker(qt_app, database, monkeypatch):
    from visionguard.runtime import VisionWorker
    database.set_passphrase("registration-worker-administrator")
    token = database.users.authenticate_admin("registration-worker-administrator")
    clock = NS(now=time.monotonic())
    monkeypatch.setattr("visionguard.runtime.time.monotonic", lambda: clock.now)
    worker = VisionWorker(Settings(), database)
    results = []
    worker.registration_result.connect(results.append)
    worker.command("register", {"token": token, "name": "Armaan"})
    worker._commands(FaceMatcher([]), GestureStabilizer())
    worker.enrolling.samples = list(np.tile(vector(0), (6, 1)))
    worker.enrolling.add = lambda *args: "READY"
    clock.now += .25
    worker.process_faces(FaceResult(80, clock.now, clock.now, [face(0)], 10), FaceMatcher([]))
    assert results[-1]["success"]
    assert database.users.list_users()[0].user_name == "Armaan"
    restarted = VisionWorker(Settings(), database)
    assert restarted.directory.identify(vector(0))[0].user_name == "Armaan"


def test_directory_invalidation_preserves_existing_owner_absence_deadline(database, registered):
    state = StateManager(Settings(auto_lock=True, absence_timeout=5.))
    owner = public_user(registered.owner)
    for now in (10., 10.2, 10.4):
        state.update_auth(Observation(True), now, face_count=1,
            identity_observation=Observation(True), identity_user=owner, directory_revision=2)
    state.update_auth(Observation(kind="absent"), 11., identity_observation=Observation(kind="absent"))
    missing_since = state.presence.missing_since
    state.context(reason="Permission edited", preserve_presence=True)
    assert state.presence.missing_since == missing_since
    snapshot, _ = state.update_auth(Observation(), time.monotonic() + 6, identity_observation=Observation())
    assert snapshot.decision.lock


def test_registered_face_overlay_shows_verified_name_without_scores(qt_app, database, registered, monkeypatch):
    import cv2
    from visionguard.runtime import VisionWorker
    clock = NS(now=100.)
    monkeypatch.setattr("visionguard.runtime.time.monotonic", lambda: clock.now)
    worker = VisionWorker(Settings(), database)
    verify_worker(worker, 0, clock)
    observation = face(0)
    worker.directory.observe([observation], clock.now, retained_id=registered.owner.user_id)
    texts = []
    monkeypatch.setattr(cv2, "putText", lambda frame, text, *args: texts.append(text))
    worker.render_preview(np.zeros((480, 640, 3), np.uint8), [observation], [], [])
    assert texts == ["VERIFIED: Owner"]
