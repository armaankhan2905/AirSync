"""Independent face inference with a one-item input mailbox and result slot."""
from dataclasses import dataclass
import logging
import threading
import time

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class FaceResult:
    sequence: int
    captured_at: float
    finished_at: float
    faces: list
    elapsed_ms: float
    error: str = ""


class FaceInferenceWorker:
    def __init__(self, engine_factory):
        self.engine_factory = engine_factory
        self.condition = threading.Condition()
        self.pending = None
        self.result = None
        self.sequence = 0
        self.stopping = False
        self.thread = None

    def start(self):
        self.thread = threading.Thread(target=self._run, name="face-inference", daemon=True)
        self.thread.start()

    def submit(self, frame, captured_at):
        with self.condition:
            self.sequence += 1
            # Replace the queued frame. The camera never mutates a published
            # frame and overlays are drawn on a separate mirrored copy.
            self.pending = (self.sequence, frame, captured_at)
            self.condition.notify()

    def latest(self):
        with self.condition:
            return self.result

    def _run(self):
        engine = None
        while True:
            with self.condition:
                self.condition.wait_for(lambda: self.stopping or self.pending is not None)
                if self.stopping:
                    return
                sequence, frame, captured_at = self.pending
                self.pending = None
            started = time.monotonic()
            faces, error = [], ""
            try:
                if started - captured_at > .8:
                    error = "Face input stale; discarded"
                else:
                    if engine is None:
                        engine = self.engine_factory()
                    faces = engine.detect(frame)
            except Exception as exc:
                # A bad frame/model call does not kill hand tracking or capture.
                error = f"Face inference failed: {type(exc).__name__}"
                log.warning("face_inference_failure type=%s", type(exc).__name__)
            finished = time.monotonic()
            with self.condition:
                self.result = FaceResult(sequence, captured_at, finished, faces,
                                         (finished - started) * 1000, error)

    def stop(self):
        with self.condition:
            self.stopping = True
            self.condition.notify()
        if self.thread:
            self.thread.join(timeout=2)
