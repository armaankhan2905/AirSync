from concurrent.futures import ThreadPoolExecutor
import pytest
from visionguard.config import Settings
from visionguard.security import Observation as O, State
from visionguard.state import StateManager


def verified(settings=None):
    state = StateManager(settings or Settings(auto_lock=True))
    for t in (10., 10.2, 10.4):
        state.update_auth(O(True, kind="matched"), t, face_count=1)
    return state


def test_brief_dropout_keeps_verified_but_does_not_extend_absence_deadline():
    s = verified()
    snap, _ = s.update_auth(O(kind="absent"), 10.6)
    assert snap.decision.state == State.VERIFIED and snap.decision.in_grace
    assert s.permit(10.6)
    snap, _ = s.update_auth(O(kind="quality"), 11.4, face_count=1)
    assert snap.decision.controls_allowed
    snap, _ = s.update_auth(O(), 11.8)
    assert not snap.decision.controls_allowed
    snap, _ = s.update_auth(O(), 15.6)
    assert snap.decision.lock


@pytest.mark.parametrize("observation", [O(unknown_count=1, kind="unknown"), O(camera_ok=False, kind="camera_error")])
def test_unknown_or_camera_error_revokes_immediately(observation):
    s = verified()
    epoch = s.snapshot().auth_epoch
    snap, _ = s.update_auth(observation, 10.6, face_count=1)
    assert not snap.decision.controls_allowed
    assert snap.auth_epoch == epoch + 1
    assert not s.permit(10.6)


def test_stale_out_of_order_result_cannot_replace_newer_camera_error():
    s = verified()
    s.update_auth(O(camera_ok=False), 11, captured_at=11)
    snap, accepted = s.update_auth(O(True), 11.1, captured_at=10.8)
    assert not accepted and snap.decision.state == State.CAMERA_ERROR
    assert not s.permit(11.1)


def test_old_capture_finishing_late_is_not_authorized():
    s = StateManager(Settings())
    snap, accepted = s.update_auth(O(True), 12, captured_at=10)
    assert accepted
    assert snap.camera == "Inference delayed"
    assert not s.permit(12)


def test_concurrent_results_and_reads_have_consistent_monotonic_revision():
    s = StateManager(Settings())
    def publish(i):
        return s.update_auth(O(True, kind="matched"), float(i), face_count=1)[0].revision
    with ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(publish, range(1, 101)))
    snap = s.snapshot()
    assert snap.captured_at == 100
    previous = snap.revision
    s.update_office(PowerPoint_running=True)
    assert s.snapshot().revision > previous
    copy = s.snapshot()
    copy.office["PowerPoint_running"] = False
    assert s.snapshot().office["PowerPoint_running"]  # snapshots never share mutable dictionaries


def test_settings_changes_revoke_queued_command_epoch(monkeypatch):
    s = verified()
    epoch = s.snapshot().auth_epoch
    monkeypatch.setattr("visionguard.state.time.monotonic", lambda: 10.5)
    s.context(reason="Settings change")
    assert s.snapshot().auth_epoch > epoch
    assert not s.permit(10.5)
    _, accepted = s.update_auth(O(True), 10.7, captured_at=10.45)
    assert not accepted


def test_grace_configuration_validated():
    with pytest.raises(ValueError, match="shorter"):
        Settings(absence_timeout=2, verification_grace=2).validate()
    with pytest.raises(ValueError):
        Settings(verification_grace=-1).validate()


def test_preview_settings_do_not_reset_absence_timer_or_authorization():
    s = verified()
    s.update_auth(O(), 11)
    missing = s.presence.missing_since
    s.configure(Settings(auto_lock=True, preview_visible=False))
    assert s.presence.missing_since == missing
    assert s.permit(11)
    snapshot, _ = s.update_auth(O(), 16)
    assert snapshot.decision.lock
