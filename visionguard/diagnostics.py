"""Safe offline diagnostics: never opens camera, sends Office commands or locks."""
import json
from pathlib import Path
import platform
import sys
import time


def self_test(report_path: Path) -> int:
    checks = {}
    try:
        import cv2
        import mediapipe
        import numpy as np
        import PySide6
        from .authentication import FaceEngine
        from .config import model_dir
        from .gestures import HandTracker
        from .models import verify_models
        from .security import LockManager
        from .storage import DPAPI
        verify_models(model_dir())
        checks["model_checksums"] = "passed"
        engine = FaceEngine(model_dir())
        frame = np.zeros((480, 640, 3), np.uint8)
        assert engine.detect(frame) == []
        embedding = engine.recognizer.feature(np.zeros((112, 112, 3), np.uint8))
        assert embedding.shape == (1, 128) and np.isfinite(embedding).all()
        checks["yunet_sface_initialization_and_blank_inference"] = "passed"
        tracker = HandTracker(model_dir() / "hand_landmarker.task")
        try:
            assert tracker.detect(frame, 1) == ([], [])
            checks["mediapipe_initialization_and_blank_inference"] = "passed"
        finally:
            tracker.close()
        if sys.platform == "win32":
            protector = DPAPI()
            assert protector.unprotect(protector.protect(b"VisionGuard diagnostic test")) == b"VisionGuard diagnostic test"
            checks["windows_dpapi_roundtrip"] = "passed"
            import pythoncom
            import win32com.client
            checks["office_com_imports_no_commands"] = "passed"
        assert LockManager(simulation=True).lock()
        checks["simulated_lock_only"] = "passed"
        checks["versions"] = {"opencv": cv2.__version__, "mediapipe": mediapipe.__version__, "pyside6": PySide6.__version__}
        success = True
    except Exception as exc:
        import traceback
        checks["error"] = f"{type(exc).__name__}: {exc}"
        checks["traceback"] = traceback.format_exc()
        success = False
    report = {"passed": success, "python": platform.python_version(), "platform": platform.platform(),
              "packaged": bool(getattr(sys, "frozen", False)), "checks": checks,
              "camera_opened": False, "office_commands_sent": False, "workstation_locked": False,
              "hardware_verification": "not performed"}
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return 0 if success else 1
