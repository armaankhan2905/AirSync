"""Geometric 21-landmark classification and neutral-rearmed temporal filtering."""
from dataclasses import dataclass
from enum import StrEnum
import math
import numpy as np


class Gesture(StrEnum):
    LEFT_L = "LEFT_L"
    RIGHT_L = "RIGHT_L"
    FIST = "FIST"
    PINCH = "PINCH"
    NEUTRAL = "NEUTRAL"
    AMBIGUOUS = "AMBIGUOUS"


@dataclass(frozen=True)
class Detection:
    gesture: Gesture
    confidence: float = 0.
    hand: str = ""


def angle(a, b, c) -> float:
    u, v = a - b, c - b
    n = np.linalg.norm(u) * np.linalg.norm(v)
    if n < 1e-9:
        return 0.
    return math.degrees(math.acos(float(np.clip(np.dot(u, v) / n, -1, 1))))


def anatomical_hand(label: str, *, mirrored_input=True, swap=False) -> str:
    # MediaPipe handedness assumes selfie/mirrored input. Tracking always receives
    # a mirrored frame in this application; preview visibility has no effect.
    if label not in {"Left", "Right"}:
        return ""
    if bool(not mirrored_input) ^ swap:
        return "Right" if label == "Left" else "Left"
    return label


def classify(points, hand: str, handedness_confidence: float, *, pinch_ratio=.23) -> Detection:
    p = np.asarray(points, dtype=float)
    if p.shape != (21, 3) or not np.isfinite(p).all() or handedness_confidence < .8 or not hand:
        return Detection(Gesture.AMBIGUOUS)
    scale = float(np.linalg.norm(p[0] - p[9]))
    if scale < 1e-5:
        return Detection(Gesture.AMBIGUOUS)
    extended, folded = [], []
    for mcp, pip, dip, tip in [(5, 6, 7, 8), (9, 10, 11, 12), (13, 14, 15, 16), (17, 18, 19, 20)]:
        bend = angle(p[mcp], p[pip], p[tip])
        extended.append(bend > 155 and np.linalg.norm(p[tip] - p[0]) > np.linalg.norm(p[pip] - p[0]) * 1.12)
        folded.append(bend < 115 and np.linalg.norm(p[tip] - p[mcp]) < scale * .75)
    pinch = np.linalg.norm(p[4] - p[8]) / scale
    if pinch < pinch_ratio and not all(folded):
        return Detection(Gesture.PINCH, min(.99, .85 + (pinch_ratio - pinch) * .4), hand)
    thumb_out = angle(p[1], p[2], p[4]) > 145 and np.linalg.norm(p[4] - p[5]) > .6 * scale
    thumb_vector, index_vector = p[4] - p[2], p[8] - p[5]
    perpendicular = angle(thumb_vector, np.zeros(3), index_vector)
    if extended[0] and all(folded[1:]) and thumb_out and 55 < perpendicular < 125:
        return Detection(Gesture.LEFT_L if hand == "Left" else Gesture.RIGHT_L,
                         min(handedness_confidence, .95), hand)
    if all(folded) and not thumb_out:
        return Detection(Gesture.FIST, min(handedness_confidence, .9), hand)
    return Detection(Gesture.NEUTRAL, handedness_confidence, hand)


class GestureStabilizer:
    def __init__(self, frames=4, cooldown=.7, threshold=.8):
        self.frames, self.cooldown, self.threshold = frames, cooldown, threshold
        self.reset()

    def reset(self):
        self.candidate = None
        self.count = 0
        self.started = 0.
        self.neutral_count = 0
        self.armed = True
        self.last_trigger = -1e9
        self.context = None
        self.reason = "Show a neutral open palm"
        self.last_detection = None

    def update(self, detections: list[Detection], now: float, *, authorized: bool,
               context=None) -> Gesture | None:
        if not authorized or context != self.context:
            self.candidate, self.count, self.neutral_count = None, 0, 0
            self.armed = False
            self.context = context
            self.reason = "Face authorization unavailable" if not authorized else "Target changed; show a neutral open palm"
            return None
        active = [d for d in detections if d.gesture != Gesture.NEUTRAL]
        # No hand and uncertain handedness never count as neutral rearming.
        neutral = bool(detections) and not active and all(d.confidence >= self.threshold for d in detections)
        if neutral:
            self.neutral_count += 1
            self.candidate, self.count = None, 0
            if self.neutral_count >= 3:
                self.armed = True
            self.reason = "Armed; form the desired gesture" if self.armed else "Hold the open palm to rearm"
            return None
        self.neutral_count = 0
        if len(active) != 1 or active[0].confidence < self.threshold or active[0].gesture == Gesture.AMBIGUOUS:
            self.candidate, self.count = None, 0
            self.reason = ("No hand detected" if not detections else "Conflicting hands" if len(active) > 1
                           else "Ambiguous hand or confidence below threshold")
            return None
        d = active[0]
        self.last_detection = d
        key = (d.gesture, d.hand)
        if key != self.candidate:
            self.candidate, self.count, self.started = key, 1, now
        else:
            self.count += 1
        hold = .45 if d.gesture in {Gesture.FIST, Gesture.PINCH} else .18
        if self.armed and self.count >= self.frames and now - self.started >= hold and now - self.last_trigger >= self.cooldown:
            self.armed = False
            self.last_trigger = now
            self.reason = "Stable gesture triggered; return to an open palm"
            return d.gesture
        self.reason = ("Return to an open palm before another action" if not self.armed
                       else "Cooldown active" if now - self.last_trigger < self.cooldown
                       else f"Stabilizing {self.count}/{self.frames} observations")
        return None


class HandTracker:
    def __init__(self, model_path):
        import mediapipe as mp
        self.mp = mp
        opts = mp.tasks.vision.HandLandmarkerOptions(
            base_options=mp.tasks.BaseOptions(model_asset_path=str(model_path)),
            running_mode=mp.tasks.vision.RunningMode.VIDEO, num_hands=2,
            min_hand_detection_confidence=.65, min_hand_presence_confidence=.65,
            min_tracking_confidence=.65)
        self.tracker = mp.tasks.vision.HandLandmarker.create_from_options(opts)
        self.timestamp = -1

    def detect(self, mirrored_frame, timestamp_ms: int, swap=False, pinch_ratio=.23):
        import cv2
        self.timestamp = max(timestamp_ms, self.timestamp + 1)
        rgb = cv2.cvtColor(mirrored_frame, cv2.COLOR_BGR2RGB)
        result = self.tracker.detect_for_video(
            self.mp.Image(image_format=self.mp.ImageFormat.SRGB, data=rgb), self.timestamp)
        detections, landmarks = [], []
        h, w = mirrored_frame.shape[:2]
        for points, categories in zip(result.hand_landmarks, result.handedness):
            category = categories[0]
            hand = anatomical_hand(category.category_name, swap=swap)
            # Correct x/y aspect ratio before measuring geometry; z uses x scale.
            coords = np.array([(p.x, p.y * h / w, p.z) for p in points])
            detections.append(classify(coords, hand, category.score, pinch_ratio=pinch_ratio))
            landmarks.append([(int(p.x * w), int(p.y * h)) for p in points])
        return detections, landmarks

    def close(self):
        self.tracker.close()
