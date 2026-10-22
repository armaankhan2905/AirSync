import numpy as np
import pytest
from visionguard.authentication import (
    Enrollment, Face, FaceMatcher, LivenessChallenge, sample_quality, unit,
)


def test_matching_embeddings(embedding):
    assert FaceMatcher(np.tile(embedding, (6, 1))).match(embedding)[0]


def test_nonmatching_rejected(embedding):
    assert not FaceMatcher(np.tile(embedding, (6, 1))).match(-embedding)[0]


def test_hysteresis_only_applies_to_already_verified_owner(embedding):
    matcher = FaceMatcher(np.tile(embedding, (6, 1)))
    borderline = np.zeros(128)
    borderline[0], borderline[1] = .51, np.sqrt(1 - .51 ** 2)
    assert not matcher.match(borderline)[0]
    assert matcher.match(borderline, retaining=True, hysteresis=.03)[0]
    assert not matcher.match(-embedding, retaining=True, hysteresis=.03)[0]


def test_continuous_owner_does_not_expire_completed_liveness_every_minute():
    c = LivenessChallenge()
    c.step, c.valid_until = 3, 60
    for now in range(1, 300):
        assert c.update(0, float(now))
    assert c.valid_until >= 359
    assert not c.update(None, 300)
    assert not c.update(None, 302)
    assert c.valid_until == 0


def test_uncertain_rejected(embedding):
    other = np.zeros(128)
    other[0], other[1] = .51, np.sqrt(1 - .51 ** 2)
    assert not FaceMatcher(np.tile(embedding, (6, 1)), .5, .03).match(other)[0]


def test_single_outlier_does_not_authenticate(embedding):
    assert not FaceMatcher(np.array([embedding, -embedding, -embedding])).match(embedding)[0]


@pytest.mark.parametrize("value", [np.zeros(128), np.full(128, np.nan), np.full(128, np.inf)])
def test_invalid_embedding_rejected(value):
    with pytest.raises(ValueError):
        unit(value)


def test_liveness_requires_random_sequence_and_multiple_frames():
    challenge = LivenessChallenge()
    t = 1.
    for target in list(challenge.sequence):
        yaw = target * .25
        assert not challenge.update(yaw, t)
        t += .2
        done = challenge.update(yaw, t)
        t += .2
    assert done
    assert challenge.valid_until > t
    assert not challenge.update(None, t)
    assert challenge.valid_until > t  # a brief dropped frame does not destroy the lease
    assert not challenge.update(None, t + 1.5)
    assert challenge.valid_until == 0


def test_liveness_identity_loss_and_expiry():
    c = LivenessChallenge()
    c.update(c.sequence[0] * .3, 1)
    c.update(c.sequence[0] * .3, 1.2)
    assert c.step == 1
    assert not c.update(0, 1.3, identity_ok=False)
    assert c.step == 0
    c.update(0, 2)
    c.update(0, 24)
    assert c.started == 24


def test_short_dropout_preserves_liveness_but_long_absence_does_not():
    c = LivenessChallenge()
    c.step, c.valid_until = 3, 100
    assert not c.update(None, 10)
    assert c.update(0, 10.5)
    assert not c.update(None, 11)
    assert not c.update(0, 13)
    assert c.valid_until == 0


def test_brief_match_failure_suspends_controls_without_repeating_completed_challenge():
    from visionguard.config import Settings
    from visionguard.security import Observation, PresenceMonitor
    c = LivenessChallenge()
    c.step, c.valid_until = 3, 100
    presence = PresenceMonitor(Settings(auto_lock=True))
    for t in (9., 9.25, 9.5):
        decision = presence.update(Observation(c.update(0, t)), t)
    assert decision.controls_allowed
    live = c.update(0, 10, identity_ok=False)
    assert not live
    assert not presence.update(Observation(live, unknown_count=1), 10).controls_allowed
    assert c.valid_until == 100
    for t in (10.25, 10.5, 10.75):
        decision = presence.update(Observation(c.update(0, t)), t)
    assert decision.controls_allowed  # fresh owner observations still required


def test_repeated_nonmatches_expire_liveness_and_cannot_delay_absence_lock():
    from visionguard.config import Settings
    from visionguard.security import Observation, PresenceMonitor
    c = LivenessChallenge()
    c.step, c.valid_until = 3, 100
    presence = PresenceMonitor(Settings(auto_lock=True, absence_timeout=2))
    for t in (10., 10.5, 11., 11.5, 12.):
        live = c.update(0, t, identity_ok=False)
        assert not live
        decision = presence.update(Observation(live, unknown_count=1), t)
        assert not decision.controls_allowed
    assert c.valid_until == 0
    assert decision.lock
    assert not c.update(0, 12.1)  # returning owner needs a new challenge


def test_stationary_frontal_photo_cannot_complete_challenge():
    c = LivenessChallenge()
    for i in range(200):
        assert not c.update(0, i * .2)
    assert c.valid_until == 0


def test_enrollment_rejects_no_face_multiple_faces_and_low_quality(embedding):
    e = Enrollment()
    f = Face((0, 0, 100, 100), embedding, 0, False, "Bad lighting")
    assert "Exactly" in e.add([], 1)
    assert "Exactly" in e.add([f, f], 2)
    assert e.add([f], 3) == "Bad lighting"
    assert not e.samples


def test_enrollment_samples_angles_and_finishes(embedding):
    e = Enrollment()
    t = 1.
    # Collect center plus both randomized challenge poses, then extra center.
    for yaw in [0, 0] + [e.challenge.sequence[0] * .3] * 2 + [e.challenge.sequence[1] * .3] * 2 + [0] * 3:
        result = e.add([Face((0, 0, 100, 100), embedding, yaw, True, "")], t)
        t += .5
    assert result == "READY"
    assert e.embeddings.shape[0] >= 6
    assert e.poses == {-1, 0, 1}


def test_enrollment_clears_on_identity_change(embedding):
    e = Enrollment()
    e.add([Face((0, 0, 100, 100), embedding, 0, True, "")], 1)
    assert "cleared" in e.add([Face((0, 0, 100, 100), -embedding, 0, True, "")], 2)
    assert not e.samples


@pytest.mark.parametrize("brightness", [0, 250])
def test_bad_light_and_blur_rejected(brightness):
    frame = np.full((480, 640, 3), brightness, np.uint8)
    row = np.array([100, 100, 120, 160, 125, 140, 180, 140, 155, 170, 130, 210, 180, 210, .99])
    assert not sample_quality(frame, row)[0]
    frame[:] = 100
    assert "blurred" in sample_quality(frame, row)[1]


def test_small_or_clipped_face_rejected():
    frame = np.zeros((480, 640, 3), np.uint8)
    assert not sample_quality(frame, np.array([-10, 0, 100, 100] + [0] * 11))[0]
    assert not sample_quality(frame, np.array([100, 100, 40, 40] + [0] * 11))[0]


def test_mild_softness_is_usable_live_but_not_for_enrollment(embedding):
    from types import SimpleNamespace as NS
    from visionguard.authentication import FaceEngine, VERIFICATION_FOCUS_MIN, BLUR_REASON
    frame = np.full((480, 640, 3), 100, np.uint8)
    frame[:, ::2] = 103  # Focus variance 36, between the live and enrollment floors.
    row = np.array([100, 100, 120, 160, 125, 140, 180, 140, 155, 170, 130, 210, 180, 210, .99])
    assert sample_quality(frame, row) == (False, BLUR_REASON)
    assert sample_quality(frame, row, minimum_focus=VERIFICATION_FOCUS_MIN)[0]
    engine = FaceEngine.__new__(FaceEngine)
    engine.detector = NS(setInputSize=lambda size: None, detect=lambda image: (None, [row]))
    engine.recognizer = NS(alignCrop=lambda *args: frame, feature=lambda image: embedding)
    face = engine.detect(frame)[0]
    assert face.usable_for_verification
    assert not face.quality
    enrollment = Enrollment()
    assert enrollment.add([face], 1) == BLUR_REASON
    assert not enrollment.samples


@pytest.mark.parametrize("brightness", [0, 100, 250])
def test_live_quality_still_rejects_severe_blur_and_bad_lighting(brightness):
    from visionguard.authentication import VERIFICATION_FOCUS_MIN
    frame = np.full((480, 640, 3), brightness, np.uint8)
    row = np.array([100, 100, 120, 160, 125, 140, 180, 140, 155, 170, 130, 210, 180, 210, .99])
    assert not sample_quality(frame, row, minimum_focus=VERIFICATION_FOCUS_MIN)[0]


def test_brief_dropout_preserves_incomplete_liveness_without_authorizing():
    challenge = LivenessChallenge()
    first, second, _ = challenge.sequence
    assert not challenge.update(first * .3, 1)
    assert not challenge.update(first * .3, 1.2)
    assert challenge.step == 1
    assert not challenge.update(None, 1.4)
    assert challenge.step == 1 and challenge.started == 1
    assert not challenge.update(second * .3, 1.6)
    assert not challenge.update(None, 1.7)
    assert challenge.count == 0  # Dropouts do not join nonconsecutive pose samples.
    assert not challenge.update(second * .3, 1.8)
    assert not challenge.update(second * .3, 2.)
    assert challenge.step == 2
    assert not challenge.update(0., 2.2)
    assert challenge.update(0., 2.4)


def test_long_dropout_and_unknown_identity_still_reset_incomplete_challenge():
    challenge = LivenessChallenge()
    for now in (1., 1.2):
        challenge.update(challenge.sequence[0] * .3, now)
    assert challenge.step == 1
    assert not challenge.update(None, 1.3)
    assert not challenge.update(None, 2.6)
    assert challenge.step == 0
    for now in (3., 3.2):
        challenge.update(challenge.sequence[0] * .3, now)
    assert challenge.step == 1
    assert not challenge.update(0., 3.3, identity_ok=False)
    assert challenge.step == 0


def test_dropouts_do_not_extend_original_liveness_deadline():
    challenge = LivenessChallenge()
    challenge.update(challenge.sequence[0] * .3, 1.)
    for now in range(2, 21):
        assert not challenge.update(None, float(now))
        assert not challenge.update(0., now + .1)
        assert challenge.started == 1.
    assert not challenge.update(None, 21.2)
    assert challenge.started is None and challenge.valid_until == 0


def test_enrollment_short_quality_dropout_preserves_progress_without_collecting_samples(embedding):
    enrollment = Enrollment()
    pose = enrollment.challenge.sequence[0] * .3
    for now in (1., 1.5):
        enrollment.add([Face((0, 0, 100, 100), embedding, pose, True, "")], now)
    assert enrollment.challenge.step == 1
    count = len(enrollment.samples)
    enrollment.add([Face((0, 0, 100, 100), embedding, 0, False, "Blurred")], 1.7)
    assert enrollment.challenge.step == 1
    assert len(enrollment.samples) == count
