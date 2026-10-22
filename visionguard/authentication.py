"""Real YuNet/SFace inference, sample quality, and an RGB head-turn challenge."""
from dataclasses import dataclass
import secrets
import numpy as np
import cv2


ENROLLMENT_FOCUS_MIN = 45.0
VERIFICATION_FOCUS_MIN = 30.0
BLUR_REASON = "Hold still; face is too blurred"


def unit(vector: np.ndarray) -> np.ndarray:
    a = np.asarray(vector, dtype=np.float32).reshape(-1)
    norm = np.linalg.norm(a)
    if not np.isfinite(a).all() or norm < 1e-6:
        raise ValueError("Invalid embedding")
    return a / norm


class FaceMatcher:
    def __init__(self, samples: np.ndarray, threshold=.5, margin=.03):
        self.samples = np.asarray([unit(s) for s in samples]) if len(samples) else np.empty((0, 128))
        self.threshold = threshold
        self.margin = margin

    def match(self, embedding: np.ndarray, *, retaining=False, hysteresis=0.) -> tuple[bool, float]:
        if not len(self.samples):
            return False, 0.
        scores = self.samples @ unit(embedding)
        # Require agreement with the closest two samples, not a single outlier.
        score = float(np.mean(np.sort(scores)[-2:]))
        threshold = self.threshold + self.margin - (hysteresis if retaining else 0.)
        return score >= threshold, score


@dataclass
class Face:
    box: tuple[int, int, int, int]
    embedding: np.ndarray
    yaw: float
    quality: bool
    reason: str
    matched: bool = False
    score: float = 0.
    verification_quality: bool | None = None
    user_id: str | None = None
    user_name: str = ""
    identity_status: str = ""
    track_id: int | None = None
    live: bool = False
    verification_prompt: str = ""

    @property
    def usable_for_verification(self) -> bool:
        # Enrollment keeps its higher quality requirement. Existing callers and
        # mocked observations use quality unless a live assessment is supplied.
        return self.quality if self.verification_quality is None else self.verification_quality


def sample_quality(frame: np.ndarray, row: np.ndarray, *, minimum_focus=ENROLLMENT_FOCUS_MIN) -> tuple[bool, str]:
    h, w = frame.shape[:2]
    x, y, fw, fh = row[:4]
    if min(fw, fh) < 90 or x < 0 or y < 0 or x + fw > w or y + fh > h:
        return False, "Move closer and keep your whole face visible"
    crop = frame[int(y):int(y + fh), int(x):int(x + fw)]
    if crop.size == 0:
        return False, "Face is outside the camera"
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    if not 45 < float(gray.mean()) < 215:
        return False, "Improve lighting; avoid strong backlight"
    eyes = row[4:8].reshape(2, 2)
    if np.linalg.norm(eyes[0] - eyes[1]) < fw * .18:
        return False, "Face angle is too extreme"
    points = row[4:14].reshape(5, 2)
    if any(not (x <= px <= x + fw and y <= py <= y + fh) for px, py in points):
        return False, "Facial landmarks are obscured"
    if float(cv2.Laplacian(gray, cv2.CV_64F).var()) < minimum_focus:
        return False, BLUR_REASON
    return True, "Good sample"


class FaceEngine:
    def __init__(self, models):
        self.detector = cv2.FaceDetectorYN.create(
            str(models / "face_detection_yunet_2023mar.onnx"), "", (640, 480), .90, .3, 5000)
        self.recognizer = cv2.FaceRecognizerSF.create(
            str(models / "face_recognition_sface_2021dec.onnx"), "")

    def detect(self, frame: np.ndarray) -> list[Face]:
        h, w = frame.shape[:2]
        self.detector.setInputSize((w, h))
        _, rows = self.detector.detect(frame)
        faces = []
        for row in ([] if rows is None else rows):
            ok, reason = sample_quality(frame, row)
            verification_quality = ok
            if not ok and reason == BLUR_REASON:
                # Only mild softness gets a separate live threshold. Lighting,
                # size, clipping, pose and landmark checks must still pass.
                verification_quality, _ = sample_quality(frame, row, minimum_focus=VERIFICATION_FOCUS_MIN)
            aligned = self.recognizer.alignCrop(frame, row)
            emb = unit(self.recognizer.feature(aligned))
            eyes = row[4:8].reshape(2, 2)
            nose = row[8:10]
            # Signed horizontal nose displacement normalized to inter-eye distance.
            yaw = float((nose[0] - eyes[:, 0].mean()) / max(np.linalg.norm(eyes[0] - eyes[1]), 1))
            faces.append(Face(tuple(int(v) for v in row[:4]), emb, yaw, ok, reason,
                              verification_quality=verification_quality))
        return faces


class LivenessChallenge:
    """Randomized turn / opposite turn / center challenge; no PAD guarantee.

    Requires consecutive pose observations, a single face, and identity agreement
    at every step. Fresh matches renew completed liveness; sustained identity
    loss, camera failure and session changes require a new challenge.
    """
    def __init__(self, grace=1.2):
        self.grace = grace
        self.reset()

    def reset(self):
        sign = secrets.choice([-1, 1])
        self.sequence = [sign, -sign, 0]
        self.step = 0
        self.count = 0
        self.started = None
        self.valid_until = 0.
        self.missing_since = None

    @property
    def prompt(self) -> str:
        if self.step >= len(self.sequence):
            return "Liveness challenge complete"
        return {1: "Turn your nose toward the RIGHT edge of the preview",
                -1: "Turn your nose toward the LEFT edge of the preview",
                0: "Look straight at the camera"}[self.sequence[self.step]]

    def update(self, yaw: float | None, now: float, identity_ok=True) -> bool:
        if not identity_ok:
            # A single failed match during motion must suspend authorization,
            # but need not discard a completed challenge. Treat it like a brief
            # detection dropout; only a fresh matching owner can use the lease.
            # An incomplete challenge still resets on any identity mismatch.
            if self.valid_until <= now:
                self.reset()
                return False
            yaw = None
        if yaw is None:
            if self.missing_since is None:
                self.missing_since = now
            # A dropout cannot authorize or advance a challenge. Preserve its
            # completed steps briefly instead of restarting on each motion-
            # blurred frame. Consecutive pose samples still start over, and the
            # original overall deadline cannot be extended by dropouts.
            expired = self.step >= len(self.sequence) and self.valid_until <= now
            timed_out = self.started is not None and now - self.started > 20
            self.count = 0
            if expired or timed_out or now - self.missing_since > self.grace:
                self.reset()
            return False
        if self.missing_since is not None and now - self.missing_since > self.grace:
            self.reset()
        self.missing_since = None
        if now < self.valid_until:
            # Fresh owner matches continuously renew an already completed
            # challenge. Only an interruption requires the user to repeat it.
            self.valid_until = max(self.valid_until, now + 60)
            return True
        if self.step >= 3 or (self.started is not None and now - self.started > 20):
            self.reset()
        if self.started is None:
            self.started = now
        target = self.sequence[self.step]
        valid = abs(yaw) < .10 if target == 0 else yaw * target > .16
        self.count = self.count + 1 if valid else 0
        if self.count >= 2:
            self.step += 1
            self.count = 0
        if self.step == 3:
            self.valid_until = now + 60
            return True
        return False


class Enrollment:
    def __init__(self):
        self.samples = []
        self.poses = set()
        self.pose_counts = {-1: 0, 0: 0, 1: 0}
        self.last_sample = -1.
        self.challenge = LivenessChallenge()

    def add(self, faces: list[Face], now: float) -> str:
        if len(faces) != 1:
            self.challenge.reset()
            return "Exactly one face must be visible"
        face = faces[0]
        if not face.quality:
            self.challenge.update(None, now)
            return face.reason
        if self.samples and float(unit(face.embedding) @ unit(self.samples[0])) < .45:
            self.samples.clear()
            self.poses.clear()
            self.pose_counts = {-1: 0, 0: 0, 1: 0}
            self.challenge.reset()
            return "Face changed; samples cleared. Start again"
        # Capture samples across challenge poses, then require successful liveness.
        pose = -1 if face.yaw < -.16 else 1 if face.yaw > .16 else 0
        if now - self.last_sample >= .4 and self.pose_counts[pose] < 4:
            self.samples.append(unit(face.embedding))
            self.poses.add(pose)
            self.pose_counts[pose] += 1
            self.last_sample = now
        live = self.challenge.update(face.yaw, now)
        if live and len(self.samples) >= 6 and len(self.poses) == 3:
            return "READY"
        if live:
            return "Keep looking at camera to finish samples"
        return f"{self.challenge.prompt} · {len(self.samples)}/6 samples"

    @property
    def embeddings(self) -> np.ndarray:
        return np.asarray(self.samples, dtype=np.float32)
