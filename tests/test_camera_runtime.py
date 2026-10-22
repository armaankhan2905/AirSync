import time
from unittest.mock import Mock, patch
import numpy as np
from visionguard.camera import CameraManager
from visionguard.config import Settings
from visionguard.security import State


def test_capture_failure_reports_error_and_cleans_up():
    cap = Mock()
    cap.isOpened.return_value = True
    cap.read.return_value = (False, None)
    camera = CameraManager(factory=lambda _: cap)
    camera.start()
    deadline = time.monotonic() + 1
    while "No camera" not in camera.latest()[2] and time.monotonic() < deadline:
        time.sleep(.01)
    camera.stop()
    assert "No camera" in camera.latest()[2]
    cap.release.assert_called()


def test_blocked_camera_reports_failure():
    cap = Mock()
    cap.isOpened.return_value = True
    cap.read.return_value = (True, np.zeros((480, 640, 3), np.uint8))
    camera = CameraManager(factory=lambda _: cap)
    camera.start()
    deadline = time.monotonic() + 1
    while "blocked" not in camera.latest()[2] and time.monotonic() < deadline:
        time.sleep(.01)
    camera.stop()
    assert "blocked" in camera.latest()[2]


def test_worker_crash_watchdog_fails_closed_without_real_lock(qt_app, database):
    from visionguard.runtime import VisionWorker
    worker = VisionWorker(Settings(auto_lock=True), database, simulation=True)
    worker.enrolled = True
    worker.last_progress = 0
    worker.lock_manager.lock = Mock(return_value=True)
    with patch("visionguard.runtime.session_locked", return_value=False):
        with patch("visionguard.runtime.time.monotonic", return_value=10):
            worker.watchdog()
        with patch("visionguard.runtime.time.monotonic", return_value=16):
            worker.watchdog()
    assert worker.decision.state == State.LOCK_PENDING
    worker.lock_manager.lock.assert_called_once()
    assert not worker.permit()


def test_gesture_gate_rejects_other_faces_and_stale_verification(qt_app, database):
    from visionguard.runtime import VisionWorker
    from visionguard.security import Observation
    worker = VisionWorker(Settings(), database)
    now = time.monotonic()
    for offset in (.4, .2, 0):
        worker.state.update_auth(Observation(True, kind="matched"), now - offset, face_count=1)
    with patch("visionguard.runtime.session_locked", return_value=False):
        assert worker.permit()
        worker.state.update_auth(Observation(True, 1, kind="multiple"), now + .01, face_count=2)
        assert not worker.permit()
        worker.state.update_auth(Observation(True, kind="matched"), now + .02, face_count=1)
        with patch("visionguard.runtime.time.monotonic", return_value=now + 2):
            assert not worker.permit()
