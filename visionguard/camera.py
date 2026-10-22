"""Bounded latest-frame capture with reconnect and freeze detection."""
import threading
import time
import cv2
import numpy as np


class CameraManager:
    def __init__(self, index=0, factory=None):
        self.index = index
        self.factory = factory or (lambda i: cv2.VideoCapture(i, cv2.CAP_DSHOW))
        self.stop_event = threading.Event()
        self.lock = threading.Lock()
        self.frame = None
        self.timestamp = 0.
        self.error = "Camera starting"
        self.thread = None

    def start(self):
        self.thread = threading.Thread(target=self._run, name="camera-capture", daemon=True)
        self.thread.start()

    def _run(self):
        cap = None
        previous = None
        changed_at = time.monotonic()
        try:
            while not self.stop_event.is_set():
                if cap is None:
                    try:
                        cap = self.factory(self.index)
                        if not cap.isOpened():
                            raise RuntimeError("Camera unavailable; check permissions or other apps")
                        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
                        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
                        cap.set(cv2.CAP_PROP_FPS, 30)
                        previous = None
                        changed_at = time.monotonic()
                    except Exception:
                        if cap:
                            cap.release()
                        cap = None
                        with self.lock:
                            self.error = "Camera unavailable; check Windows camera privacy and other apps"
                        self.stop_event.wait(2)
                        continue
                tick = time.monotonic()
                try:
                    ok, frame = cap.read()
                    if not ok or frame is None or not frame.size:
                        raise RuntimeError("No camera frame received")
                    signature = cv2.resize(frame, (32, 24))
                    if previous is None or not np.array_equal(signature, previous):
                        changed_at = tick
                    previous = signature
                    if tick - changed_at > 3:
                        raise RuntimeError("Camera feed frozen or blocked")
                    if float(frame.std()) < 2:
                        raise RuntimeError("Camera is blocked or too dark")
                    with self.lock:
                        self.frame, self.timestamp, self.error = frame, time.monotonic(), ""
                except Exception as exc:
                    with self.lock:
                        self.error = str(exc)
                    cap.release()
                    cap = None
                    self.stop_event.wait(1)
                self.stop_event.wait(max(0, 1 / 30 - (time.monotonic() - tick)))
        finally:
            if cap is not None:
                cap.release()

    def latest(self):
        with self.lock:
            return self.frame, self.timestamp, self.error

    def stop(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=2)
