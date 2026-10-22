import pytest
from visionguard.config import Settings
from visionguard.security import Observation as O, PresenceMonitor, State


def monitor(**kwargs):
    return PresenceMonitor(Settings(auto_lock=True, **kwargs))


def verify(m, t=0):
    for i in range(3):
        decision = m.update(O(authorized=True), t + i * .2)
    return decision


def test_verified_user_never_locks():
    m = monitor()
    for i in range(100):
        d = m.update(O(authorized=True), i * .2)
        assert not d.lock
    assert d.state == State.VERIFIED and d.controls_allowed


def test_multiple_consistent_observations_required():
    m = monitor()
    assert not m.update(O(True), 0).controls_allowed
    assert not m.update(O(True), .2).controls_allowed
    assert m.update(O(True), .4).controls_allowed


@pytest.mark.parametrize("observation", [O(), O(unknown_count=1), O(unknown_count=3)])
def test_absence_unknown_replacement_lock_after_timeout(observation):
    m = monitor()
    verify(m)
    assert not m.update(observation, 1).lock
    assert not m.update(observation, 5.9).lock
    assert m.update(observation, 6).lock
    assert not m.update(observation, 7).lock  # no repeated lock calls


def test_brief_dropout_and_uncertain_identity_do_not_lock():
    m = monitor()
    verify(m)
    m.update(O(), 1)
    assert verify(m, 2).state == State.VERIFIED
    assert not m.update(O(True), 7).lock


def test_single_returning_match_cannot_reset_timer():
    m = monitor()
    verify(m)
    m.update(O(), 1)
    m.update(O(True), 3)
    assert m.update(O(), 6).lock


def test_additional_faces_relaxed_policy():
    m = monitor()
    for i in range(5):
        d = m.update(O(True, 2), i)
    assert d.state == State.VERIFIED


def test_additional_faces_strict_policy():
    m = monitor(strict_privacy=True)
    verify(m)
    m.update(O(True, 1), 1)
    assert m.update(O(True, 1), 6).lock


@pytest.mark.parametrize("policy,locks", [("lock", True), ("alert", False)])
def test_camera_failure_policy(policy, locks):
    m = monitor(camera_policy=policy)
    m.update(O(camera_ok=False), 0)
    d = m.update(O(camera_ok=False), 5)
    assert d.lock is locks
    assert not d.controls_allowed


def test_unknown_faces_never_reset_absence_clock():
    m = monitor()
    verify(m)
    m.update(O(), 1)
    m.update(O(unknown_count=1), 3)
    assert m.update(O(unknown_count=2), 6).lock


def test_disabled_unenrolled_paused_locked_states():
    m = monitor()
    assert m.update(O(), 0, enrolled=False).state == State.UNENROLLED
    assert m.update(O(True), 1, paused=True).state == State.PAUSED
    assert not m.update(O(True), 10, locked=True).controls_allowed
    m.settings.auto_lock = False
    assert not m.update(O(), 100).lock
    assert not m.update(O(), 200).lock


def test_failed_lock_can_retry():
    m = monitor()
    m.update(O(), 0)
    assert m.update(O(), 6).lock
    m.lock_failed()
    assert m.update(O(), 8).lock
