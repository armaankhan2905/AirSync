"""Pure, bounded, slide-scoped ink and gesture arbitration (no Office writes)."""
from dataclasses import dataclass, field
import math
import threading
import numpy as np
from .gestures import Gesture


@dataclass(frozen=True)
class SlideContext:
    session: str
    slide: int | str
    hwnd: int
    rect: tuple  # Physical screen pixels: x, y, width, height of slide content.
    observed_at: float
    visible: bool = True

    @property
    def key(self):
        return self.session, self.slide


def fit_slide(rect, aspect):
    x, y, w, h = rect
    if w <= 0 or h <= 0 or not math.isfinite(aspect) or aspect <= 0:
        raise ValueError("Invalid slide geometry")
    width, height = min(w, h * aspect), min(h, w / aspect)
    return round(x + (w - width) / 2), round(y + (h - height) / 2), max(1, round(width)), max(1, round(height))


class CoordinateMapper:
    """Four calibrated camera corners -> normalized slide, independent of DPI."""
    def __init__(self, corners):
        p = np.asarray(corners, dtype=float)
        if p.shape != (4, 2) or not np.isfinite(p).all() or np.any(p < 0) or np.any(p > 1):
            raise ValueError("Calibration needs four camera points between 0 and 1")
        edges = np.roll(p, -1, axis=0) - p
        cross = edges[:, 0] * np.roll(edges[:, 1], -1) - edges[:, 1] * np.roll(edges[:, 0], -1)
        area = .5 * abs(np.dot(p[:, 0], np.roll(p[:, 1], -1)) - np.dot(p[:, 1], np.roll(p[:, 0], -1)))
        if np.any(cross <= .01) or np.any(np.linalg.norm(edges, axis=1) < .15) or area < .04:
            raise ValueError("Capture a larger, clockwise rectangle: top left, top right, bottom right, bottom left")
        rows, values = [], []
        for (x, y), (u, v) in zip(p, ((0, 0), (1, 0), (1, 1), (0, 1))):
            rows.extend(((x, y, 1, 0, 0, 0, -u*x, -u*y), (0, 0, 0, x, y, 1, -v*x, -v*y)))
            values.extend((u, v))
        self.matrix = np.append(np.linalg.solve(rows, values), 1).reshape(3, 3)

    def map(self, point, *, mirrored=True):
        x, y = point
        if not math.isfinite(x) or not math.isfinite(y):
            return None
        v = self.matrix @ (x if mirrored else 1 - x, y, 1)
        if abs(v[2]) < 1e-8:
            return None
        u, v = v[:2] / v[2]
        # Leaving the calibrated region lifts the pen rather than tracing edges.
        if not (-1e-8 <= u <= 1 + 1e-8 and -1e-8 <= v <= 1 + 1e-8):
            return None
        return float(np.clip(u, 0, 1)), float(np.clip(v, 0, 1))


class TrajectoryFilter:
    def __init__(self, smoothing=.45):
        self.smoothing = smoothing
        self.reset()

    def reset(self):
        self.point = None
        self.at = None

    def update(self, point, now):
        if self.point is None or now - self.at > .2:
            self.point = point
        else:
            # Time-based EMA: consistent feel across camera frame rates.
            tau = .005 + .09 * self.smoothing
            alpha = 1 - math.exp(-max(.001, now - self.at) / tau)
            self.point = tuple(a + alpha * (b-a) for a, b in zip(self.point, point))
        self.at = now
        return self.point


@dataclass
class Stroke:
    color: str
    width: float
    points: list = field(default_factory=list)


class AirWriting:
    MAX_POINTS = 50000
    MAX_SLIDE_POINTS = 10000

    def __init__(self, settings):
        self.lock = threading.RLock()
        self.ink = {}
        self.revision = 0
        self.generation = 0
        self.mode = False
        self.context = None
        self.epoch = None
        self.stroke = None
        self.last_input = -1e9
        self.feedback = ""
        self.reason = "Show two fingers to rearm"
        self.last_detection = None
        self.reset_gate()
        self.configure(settings)

    def configure(self, settings):
        with self.lock:
            self.settings = settings
            self.mapper = CoordinateMapper(settings.writing_calibration)
            self.filter = TrajectoryFilter(settings.writing_smoothing)
            self.suspend(disable=True)

    def reset_gate(self):
        self.armed = False
        self.neutrals = 0
        self.candidate = None
        self.count = 0
        self.started = 0.
        self.last_trigger = -1e9

    def end_stroke(self):
        self.stroke = None
        self.filter.reset()
        self.draw_candidate = None
        self.draw_count = 0

    def suspend(self, *, disable=False):
        with self.lock:
            self.end_stroke()
            self.reset_gate()
            if disable and self.mode:
                self.mode = False
                self.feedback = "Writing Mode OFF"
                self.revision += 1

    def reconcile(self, contexts, now, *, known=True):
        """Only confirmed show closure removes ink; transient COM errors hide it."""
        with self.lock:
            if known:
                sessions = {c.session for c in contexts}
                removed = [key for key in self.ink if key[0] not in sessions]
                for key in removed:
                    del self.ink[key]
                if removed:
                    self.revision += 1
                    self.generation += 1
                if self.context and self.context.session not in sessions:
                    self.suspend(disable=True)
                    self.context = None
            if now - self.last_input > .2:
                self.end_stroke()

    def clear(self, key, *, all_slides=False):
        # Called only behind runtime authorization, never writes to COM.
        with self.lock:
            if all_slides:
                self.ink.clear()
            else:
                self.ink.pop(key, None)
            self.end_stroke()
            self.revision += 1
            self.generation += 1
            self.feedback = "Annotations Cleared"

    def snapshot(self):
        with self.lock:
            return {"mode": self.mode, "context": self.context, "revision": self.revision, "generation": self.generation,
                    "strokes": tuple((s.color, s.width, tuple(s.points)) for s in self.ink.get(self.context.key, ())) if self.context else (),
                    "feedback": self.feedback}

    def update(self, detections, point, now, *, context, authorized, epoch):
        """Own ALL PowerPoint gestures when enabled; at most one action per frame."""
        with self.lock:
            self.feedback = ""
            if not authorized or not context or now - context.observed_at > .25:
                self.suspend(disable=True)
                self.reason = "Writing requires a present verified user and a focused slideshow"
                return None
            changed = self.context is None or context.key != self.context.key or epoch != self.epoch
            if changed or now - self.last_input > .2:
                if self.context and (context.session != self.context.session or epoch != self.epoch):
                    self.mode = False
                self.end_stroke()
                self.reset_gate()
            self.context, self.epoch, self.last_input = context, epoch, now
            if len(detections) != 1 or detections[0].confidence < self.settings.writing_confidence:
                self.end_stroke()
                self.candidate, self.count, self.neutrals = None, 0, 0
                return None
            d = detections[0]
            g = d.gesture
            self.last_detection = d
            neutral = g == Gesture.TWO_FINGERS or (not self.mode and g == Gesture.OPEN_PALM)
            if neutral:
                self.end_stroke()
                self.candidate, self.count = None, 0
                self.neutrals += 1
                if self.neutrals >= 3:
                    self.armed = True
                self.reason = "Ready" if self.armed else "Hold two fingers to rearm"
                return None
            self.neutrals = 0
            if self.mode and g == Gesture.INDEX:
                self.candidate, self.count = None, 0
                if not self.armed or point is None:
                    self.end_stroke()
                    return None
                mapped = self.mapper.map(point)
                if mapped is None:
                    self.end_stroke()
                    return None
                if self.draw_candidate != d.hand:
                    self.end_stroke()
                    self.draw_candidate = d.hand
                self.draw_count += 1
                if self.draw_count < 2:
                    return None
                self.draw(mapped, now)
                return None
            if self.stroke is not None:
                self.armed = False
            self.end_stroke()
            allowed = {Gesture.THREE_FINGERS, Gesture.FIST, Gesture.PINCH}
            allowed |= {Gesture.OPEN_PALM} if self.mode else {Gesture.LEFT_L, Gesture.RIGHT_L}
            if g not in allowed:
                self.candidate, self.count = None, 0
                return None
            key = (g, d.hand)
            if key != self.candidate:
                self.candidate, self.count, self.started = key, 1, now
            else:
                self.count += 1
            hold = self.settings.writing_hold if g in {Gesture.THREE_FINGERS, Gesture.OPEN_PALM} else .45 if g in {Gesture.FIST, Gesture.PINCH} else .18
            if not self.armed or self.count < self.settings.gesture_frames or now - self.started < hold or now - self.last_trigger < self.settings.gesture_cooldown:
                self.reason = "Hold gesture" if self.armed else "Show two fingers to rearm"
                return None
            self.armed = False
            self.last_trigger = now
            if g == Gesture.THREE_FINGERS:
                self.mode = not self.mode
                self.revision += 1
                self.feedback = "Writing Mode ON" if self.mode else "Writing Mode OFF"
            elif g == Gesture.OPEN_PALM:
                self.clear(context.key)
            else:
                if g in {Gesture.FIST, Gesture.PINCH}:
                    self.mode = False
                return g
            return None

    def draw(self, point, now):
        strokes = self.ink.setdefault(self.context.key, [])
        total = sum(len(s.points) for group in self.ink.values() for s in group)
        if total >= self.MAX_POINTS or sum(len(s.points) for s in strokes) >= self.MAX_SLIDE_POINTS:
            self.feedback = "Ink limit reached; clear annotations to continue"
            self.end_stroke()
            return
        point = self.filter.update(point, now)
        if self.stroke and math.dist(point, self.stroke.points[-1]) > .25:
            # Tracking jumps must not create a line across the slide.
            self.stroke = None
            self.filter.reset()
        if self.stroke is None:
            self.stroke = Stroke(self.settings.writing_color, self.settings.writing_width)
            strokes.append(self.stroke)
        if not self.stroke.points or math.dist(point, self.stroke.points[-1]) > .0005:
            self.stroke.points.append(point)
            self.revision += 1
