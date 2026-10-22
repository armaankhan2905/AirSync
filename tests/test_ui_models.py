from pathlib import Path
import numpy as np
import pytest
from visionguard.config import Settings, model_dir


def test_camera_first_window_constructs_without_camera_or_office(qt_app, database):
    from PySide6.QtWidgets import QListWidget, QStackedWidget
    from visionguard.camera_view import CameraView
    from visionguard.ui import MainWindow, STYLE
    qt_app.setStyleSheet(STYLE)
    window = MainWindow(database, Settings(), smoke=True)
    window.show()
    qt_app.processEvents()
    assert isinstance(window.centralWidget(), CameraView)
    assert not window.findChildren(QListWidget)
    assert not window.findChildren(QStackedWidget)
    assert window.preview.width() * window.preview.height() >= .9 * window.width() * window.height()
    assert window.worker is None
    assert window.enroll_button.isVisible()
    assert not window.cancel_enrollment_button.isVisible()
    assert not window.icon.isNull()
    window.timer.stop()
    window.close()


def test_first_launch_wizard_validates_consent_and_passphrase(qt_app):
    from visionguard.ui import SetupWizard
    wizard = SetupWizard()
    wizard.validate()
    assert "consent" in wizard.error.text()
    wizard.consent.setChecked(True)
    wizard.password.setText("correct-application-passphrase")
    wizard.confirm.setText("different-passphrase")
    wizard.validate()
    assert "matching" in wizard.error.text()
    wizard.confirm.setText("correct-application-passphrase")
    wizard.validate()
    assert wizard.result() == 1


@pytest.mark.skipif(not (model_dir() / "hand_landmarker.task").exists(), reason="Run model setup first")
def test_real_models_load_and_infer_without_webcam():
    from visionguard.authentication import FaceEngine
    from visionguard.gestures import HandTracker
    engine = FaceEngine(model_dir())
    assert engine.detect(np.zeros((480, 640, 3), np.uint8)) == []
    embedding = engine.recognizer.feature(np.zeros((112, 112, 3), np.uint8))
    assert embedding.shape == (1, 128) and np.isfinite(embedding).all()
    tracker = HandTracker(model_dir() / "hand_landmarker.task")
    try:
        detections, points = tracker.detect(np.zeros((480, 640, 3), np.uint8), 1)
        assert detections == [] and points == []
    finally:
        tracker.close()


def test_missing_and_corrupt_models_fail_integrity_check(tmp_path):
    import json
    from visionguard.models import verify_models
    manifest = [{"file": f"model{i}.onnx", "sha256": "wrong"} for i in range(3)]
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(RuntimeError, match="missing"):
        verify_models(tmp_path)
    (tmp_path / "model0.onnx").write_bytes(b"corrupted")
    with pytest.raises(RuntimeError, match="integrity"):
        verify_models(tmp_path)


def test_GUI_ignores_old_auth_revision_and_replaced_worker(qt_app, database):
    from types import SimpleNamespace
    from visionguard.ui import MainWindow
    window = MainWindow(database, Settings(), smoke=True)
    window.timer.stop()
    window.worker = SimpleNamespace(worker_id="new")
    window.on_status({"worker_id": "new", "revision": 10, "state": "CAMERA_ERROR"})
    window.on_status({"worker_id": "new", "revision": 9, "state": "VERIFIED"})
    assert window.state_label.toolTip() == "CAMERA ERROR"
    window.on_status({"worker_id": "old", "revision": 1000, "state": "VERIFIED"})
    assert window.state_label.toolTip() == "CAMERA ERROR"
    window.worker = None
    window.close()
