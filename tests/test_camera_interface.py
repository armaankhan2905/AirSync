"""Camera-first GUI regressions. Hardware, Office and lock APIs stay mocked."""
from types import SimpleNamespace as NS
from unittest.mock import Mock
import time
import numpy as np
import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage
from PySide6.QtTest import QTest
from visionguard.config import Settings


@pytest.fixture
def window(qt_app, database):
    from visionguard.ui import MainWindow, STYLE
    qt_app.setStyleSheet(STYLE)
    view = MainWindow(database, Settings(), smoke=True)
    view.timer.stop()
    view.show()
    qt_app.processEvents()
    yield view
    view.worker = None
    if view.settings_dialog:
        view.settings_dialog.reject()
    if view.registration_dialog:
        view.registration_dialog.reject()
    if view.manage_users_dialog:
        view.manage_users_dialog.reject()
    view.close()
    qt_app.processEvents()


@pytest.mark.parametrize("size", [(800, 600), (1180, 860), (1920, 1080), (850, 1200)])
def test_preview_resizes_without_cropping_or_stretching(window, qt_app, size):
    image = QImage(640, 480, QImage.Format.Format_RGB888)
    image.fill(Qt.GlobalColor.darkCyan)
    window.on_image(image)
    window.resize(*size)
    qt_app.processEvents()
    rect = window.preview.image_rect()
    assert rect.width() / rect.height() == pytest.approx(4 / 3, abs=.003)
    assert rect.width() <= window.preview.width()
    assert rect.height() <= window.preview.height()
    assert rect.left() >= 0 and rect.top() >= 0
    for overlay in (window.top_bar, window.activity_bar, window.guide):
        assert window.preview.rect().contains(overlay.geometry())
    assert window.top_bar.layout().minimumSize().width() <= window.top_bar.width()


@pytest.mark.parametrize("values, expected", [
    ({"state": "VERIFIED", "face_status": "matched"}, "VERIFIED"),
    ({"state": "UNKNOWN_PERSON", "face_status": "unknown"}, "UNKNOWN"),
    ({"state": "TEMPORARILY_MISSING", "face_status": "challenge"}, "VERIFYING"),
    ({"state": "TEMPORARILY_MISSING", "face_status": "absent"}, "USER ABSENT"),
])
def test_compact_status_and_no_technical_scores(window, values, expected):
    window.on_status({**values, "enrolled": True, "target": "PowerPoint", "camera": "Connected"})
    assert window.state_label.text() == expected
    assert window.app_label.text() == "POWERPOINT"
    assert not window.enroll_button.isVisible()


def test_unenrolled_and_enrollment_progress_use_camera_overlay(window, database, monkeypatch):
    database.set_passphrase("test-administrator-passphrase")
    token = database.users.authenticate_admin("test-administrator-passphrase")
    monkeypatch.setattr(window, "request_admin_session", lambda: token)
    window.worker = NS(worker_id="current", isRunning=lambda: True, command=Mock())
    assert window.begin_enrollment()
    dialog = window.registration_dialog
    dialog.name.setText("Owner")
    assert window.start_registration(dialog)
    assert window.worker.command.call_args.args[0] == "register"
    window.on_status({"enrolled": False, "enrolling": True, "face_status": "enrollment",
                      "prompt": "Turn left · 3/6 samples", "camera": "Connected"})
    assert dialog.prompt.text() == "Turn left · 3/6 samples"
    assert not window.guide.isVisible()
    assert not window.enroll_button.isVisible()
    window.on_registration_result({"worker_id": "current", "request_id": dialog.request_id,
                                   "success": True, "message": "User registered successfully."})
    assert dialog.completed and dialog.progress.value() == 100
    assert window.toast.title.text() == "User registered successfully."


def test_reenrollment_still_requires_authorization(window, database, embedding, monkeypatch):
    database.enroll(np.tile(embedding, (6, 1)))
    window.worker = NS(isRunning=lambda: True, command=Mock())
    authorize = Mock(return_value=False)
    monkeypatch.setattr(window, "request_admin_session", authorize)
    assert not window.begin_enrollment()
    authorize.assert_called_once()
    window.worker.command.assert_not_called()


def test_settings_cancel_does_not_mutate_live_settings_and_save_is_protected(window, monkeypatch):
    window.settings_button.click()
    assert window.settings_dialog.isVisible()
    window.widgets["auto_lock"].setChecked(True)
    window.widgets["absence_timeout"].setValue(12)
    assert not window.settings.auto_lock
    monkeypatch.setattr(window, "authorize", lambda: False)
    assert not window.save_settings()
    assert not window.settings.auto_lock
    assert window.settings.absence_timeout == 5
    window.settings_dialog.reject()
    window.open_settings()
    assert not window.widgets["auto_lock"].isChecked()
    monkeypatch.setattr(window, "authorize", lambda: True)
    window.widgets["auto_lock"].setChecked(True)
    assert window.save_settings()
    assert window.settings.auto_lock


def test_dynamic_gesture_status_and_optional_diagnostics_stay_out_of_main_view(window, qt_app):
    window.open_settings()
    window.widgets["developer_diagnostics"].setChecked(True)
    # More options stays collapsed until explicitly requested.
    assert not window.diagnostic_panel.isVisible()
    window.diagnostic_panel.parentWidget().show()
    qt_app.processEvents()
    window.on_status({"gestures_enabled": False, "diagnostics": {"face": {"state": "VERIFYING"}}})
    assert window.gestures_status.text() == "GESTURES OFF"
    assert "VERIFYING" in window.diagnostic_panel.toPlainText()
    window.settings_dialog.reject()
    assert window.diagnostic_panel is None


def test_close_to_tray_does_not_stop_monitoring(window, monkeypatch):
    window.worker = NS(stop=Mock())
    window.smoke = False
    monkeypatch.setattr(window.tray, "isVisible", lambda: True)
    window.close()
    assert not window.isVisible()
    window.worker.stop.assert_not_called()
    window.smoke = True


def test_simulation_cannot_be_disabled_by_settings(qt_app, database, monkeypatch):
    from visionguard.ui import MainWindow
    view = MainWindow(database, Settings(), smoke=True, force_simulation=True)
    view.timer.stop()
    view.open_settings()
    assert not view.widgets["development_simulation"].isEnabled()
    view.widgets["development_simulation"].setChecked(False)
    monkeypatch.setattr(view, "authorize", lambda: True)
    assert view.save_settings()
    assert view.settings.development_simulation
    view.settings_dialog.reject()
    view.close()


def active_gesture(**values):
    return {"state": "VERIFIED", "camera": "Connected", "enrolled": True,
            "gestures_enabled": True, "target": "PowerPoint", "target_ready": True,
            "gesture_details": {"detected": ["RIGHT_L"], "confidence": [.99], "authorized": True}, **values}


def test_detected_gesture_never_claims_executed_action(window):
    window.on_status(active_gesture())
    assert "detected" in window.activity.text()
    assert window.toast.isHidden()
    assert "executed" not in window.activity.text()


def test_confirmed_actions_repeat_per_execution_and_expire(window):
    window.worker = NS(worker_id="current")
    feedback = {"worker_id": "current", "gesture": "RIGHT_L", "result": "PowerPoint: next", "success": True}
    window.on_command_result(feedback)
    window.on_command_result(feedback)
    assert window.toast.title.text() == "NEXT SLIDE"
    assert window.activity.text() == "Next slide executed"
    assert len([event for event in window.events if "Next slide executed" in event]) == 2
    assert window.toast.isVisible()
    QTest.qWait(2050)
    assert window.toast.isHidden()


def test_late_results_from_replaced_worker_are_ignored(window):
    window.worker = NS(worker_id="new")
    window.on_command_result({"worker_id": "old", "result": "PowerPoint: next", "success": True})
    assert window.toast.isHidden()


@pytest.mark.parametrize("values, message", [
    ({"gestures_enabled": False}, "GESTURE CONTROL DISABLED"),
    ({"gesture_details": {"detected": ["RIGHT_L"], "confidence": [.99], "authorized": False}}, "USER NOT VERIFIED"),
    ({"target_ready": False}, "NO ACTIVE SLIDESHOW"),
    ({"target_ready": False, "office": "COM unavailable"}, "OFFICE CONNECTION ERROR"),
])
def test_blocked_gesture_explains_once_per_hold(window, monkeypatch, values, message):
    present = Mock()
    monkeypatch.setattr(window.toast, "present", present)
    for _ in range(10):
        window.on_status(active_gesture(**values))
    present.assert_called_once()
    assert present.call_args.args[1] == message


def test_failed_command_and_slide_boundary_do_not_claim_success(window):
    window.worker = NS(worker_id="current")
    for result, expected in [("Office command failed: COMError", "OFFICE CONNECTION ERROR"),
                             ("Already at last slide", "Already at last slide")]:
        window.on_command_result({"worker_id": "current", "gesture": "RIGHT_L", "result": result, "success": False})
        assert window.toast.title.text() == expected
        assert "executed" not in window.activity.text()


def test_minimized_window_continues_consuming_frames_and_office_results(window, qt_app):
    frame = QImage(640, 480, QImage.Format.Format_RGB888)
    frame.fill(Qt.GlobalColor.darkGreen)
    worker = NS(worker_id="current", watchdog=Mock(), view=lambda: active_gesture(worker_id="current", revision=1),
                take_image=lambda: frame)
    window.worker = worker
    window.hide()
    window.heartbeat()
    worker.watchdog.assert_called_once()
    assert not window.preview._pixmap.isNull()
    assert not window.isVisible()
    window.on_command_result({"worker_id": "current", "gesture": "RIGHT_L", "result": "PowerPoint: next", "success": True})
    assert not window.isVisible()
    window.showNormal()
    qt_app.processEvents()
    assert window.activity.text() == "Next slide executed"


def test_only_five_recent_events_and_camera_failure_clears_frame(window):
    frame = QImage(640, 480, QImage.Format.Format_RGB888)
    window.on_image(frame)
    window.on_status({"camera": "Disconnected", "state": "CAMERA_ERROR"})
    assert window.preview._pixmap.isNull()
    window.on_image(frame)  # A previously queued frame must not hide the outage.
    assert window.preview._pixmap.isNull()
    for i in range(10):
        window.add_event(f"Event {i}")
    assert len(window.events) == 5
    assert window.activity.text() == "Event 9"


def test_face_boxes_use_each_identity_and_hand_lines_use_mirrored_coordinates(qt_app, database, embedding, monkeypatch):
    import cv2
    from visionguard.runtime import VisionWorker
    from visionguard.authentication import Face
    from visionguard.security import Observation
    from visionguard.gestures import Detection, Gesture
    worker = VisionWorker(Settings(), database)
    worker.multi_user = False  # Legacy rendering remains supported for migrated-profile tests.
    now = time.monotonic()
    for i in range(3):
        worker.state.update_auth(Observation(True), now - .2 + i * .1, face_count=1)
    faces = [Face((30, 80, 100, 120), embedding, 0, True, "", matched=True),
             Face((200, 80, 100, 120), -embedding, 0, True, "", matched=False)]
    frame = np.zeros((480, 640, 3), np.uint8)
    rectangles, labels, lines = [], [], []
    monkeypatch.setattr(cv2, "rectangle", lambda *args: rectangles.append(args))
    monkeypatch.setattr(cv2, "putText", lambda *args: labels.append(args))
    monkeypatch.setattr(cv2, "line", lambda *args: lines.append(args))
    points = [(30 + i * 5, 300 + i * 2) for i in range(21)]
    worker.render_preview(frame, faces, [Detection(Gesture.RIGHT_L, .99, "Right")], [points])
    assert rectangles[0][1:3] == ((510, 80), (610, 200))
    assert [item[1] for item in labels] == ["Verified", "Unknown", "RIGHT L"]
    assert rectangles[0][3] != rectangles[2][3]  # An unknown extra face cannot inherit green.
    assert len(lines) == 21
    assert lines[0][1:3] == (points[0], points[1])
    assert not worker.take_image().isNull()
    assert worker.take_image() is None  # Only one latest preview is retained.


def test_stale_face_detections_are_not_drawn(qt_app, database, embedding, monkeypatch):
    import cv2
    from visionguard.runtime import VisionWorker
    from visionguard.authentication import Face
    worker = VisionWorker(Settings(), database)
    draw = Mock()
    monkeypatch.setattr(cv2, "rectangle", draw)
    worker.render_preview(np.zeros((480, 640, 3), np.uint8),
                          [Face((10, 10, 100, 100), embedding, 0, True, "")], [], [])
    draw.assert_not_called()
