import threading
import time
import numpy as np
from visionguard.face_worker import FaceInferenceWorker


def wait_result(worker, sequence):
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        result = worker.latest()
        if result and result.sequence >= sequence:
            return result
        time.sleep(.005)
    raise AssertionError("Face worker did not finish")


def test_face_exception_is_reported_and_next_frame_recovers():
    class Engine:
        calls = 0
        def detect(self, frame):
            self.calls += 1
            if self.calls == 1:
                raise ValueError("bad inference")
            return ["recovered"]
    worker = FaceInferenceWorker(Engine)
    worker.start()
    try:
        worker.submit(np.zeros((2, 2, 3)), time.monotonic())
        assert "ValueError" in wait_result(worker, 1).error
        worker.submit(np.zeros((2, 2, 3)), time.monotonic())
        assert wait_result(worker, 2).faces == ["recovered"]
        assert worker.thread.is_alive()
    finally:
        worker.stop()


def test_slow_face_inference_keeps_only_latest_pending_frame():
    entered, release = threading.Event(), threading.Event()
    seen = []
    class Engine:
        def detect(self, frame):
            seen.append(frame)
            if len(seen) == 1:
                entered.set()
                assert release.wait(2)
            return []
    worker = FaceInferenceWorker(Engine)
    worker.start()
    try:
        worker.submit(1, time.monotonic())
        assert entered.wait(1)
        worker.submit(2, time.monotonic())
        worker.submit(3, time.monotonic())
        release.set()
        wait_result(worker, 3)
        assert seen == [1, 3]
    finally:
        release.set()
        worker.stop()


def test_stale_face_input_is_discarded_without_model_call():
    calls = []
    worker = FaceInferenceWorker(lambda: calls.append("created"))
    worker.start()
    try:
        worker.submit(None, time.monotonic() - 2)
        assert "stale" in wait_result(worker, 1).error
        assert not calls
    finally:
        worker.stop()
