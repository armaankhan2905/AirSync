from types import SimpleNamespace as NS
from unittest.mock import Mock
import numpy as np
import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QListWidget, QStackedWidget, QMessageBox
from visionguard.config import Settings
from visionguard.users import Permissions


@pytest.fixture
def ui(qt_app, database, monkeypatch):
    from visionguard.ui import MainWindow, STYLE
    qt_app.setStyleSheet(STYLE)
    database.set_passphrase("gui-administrator-test-passphrase")
    token = database.users.authenticate_admin("gui-administrator-test-passphrase")
    owner = database.users.register("Armaan", np.tile(np.eye(128)[0], (6, 1)), token)
    guest = database.users.register("Presenter", np.tile(np.eye(128)[1], (6, 1)), token)
    view = MainWindow(database, Settings(), smoke=True)
    view.timer.stop()
    view.worker = NS(worker_id="current", isRunning=lambda: True, command=Mock(), invalidate=Mock())
    monkeypatch.setattr(view, "request_admin_session", lambda: token)
    view.show()
    qt_app.processEvents()
    yield NS(window=view, token=token, owner=owner, guest=guest)
    if view.registration_dialog:
        view.registration_dialog.reject()
    if view.manage_users_dialog:
        view.manage_users_dialog.reject()
    if view.settings_dialog:
        view.settings_dialog.reject()
    view.worker = None
    view.close()
    qt_app.processEvents()


def test_registration_button_opens_authorized_compact_dialog_without_extra_workers(ui):
    w = ui.window
    w.register_button.click()
    dialog = w.registration_dialog
    assert dialog.isVisible()
    assert not w.findChildren(QListWidget) and not w.findChildren(QStackedWidget)
    assert dialog.name.maxLength() == 60
    assert not dialog.capture.isEnabled()
    dialog.update_status({"camera": "Connected", "face_count": 2})
    assert not dialog.capture.isEnabled()
    dialog.update_status({"camera": "Connected", "face_count": 1})
    assert dialog.capture.isEnabled()
    dialog.name.setText("New presenter")
    dialog.capture.click()
    request = w.worker.command.call_args.args
    assert request[0] == "register" and request[1]["name"] == "New presenter"
    assert request[1]["token"] == ui.token and request[1]["request_id"] == dialog.request_id


def test_unregistered_user_cannot_open_registration_or_management_without_admin(ui, monkeypatch):
    w = ui.window
    monkeypatch.setattr(w, "request_admin_session", lambda: None)
    assert not w.begin_enrollment()
    assert not w.open_manage_users()
    assert w.registration_dialog is None and w.manage_users_dialog is None
    w.worker.command.assert_not_called()


def test_registration_reuses_latest_camera_frame_even_when_main_preview_hidden(ui):
    w = ui.window
    w.settings.preview_visible = False
    w.open_registration(token=ui.token)
    w.last_status = {"camera": "Connected"}
    image = QImage(640, 480, QImage.Format.Format_RGB888)
    image.fill(Qt.GlobalColor.darkCyan)
    w.on_image(image)
    assert w.preview._pixmap.isNull()
    assert not w.registration_dialog.preview._pixmap.isNull()
    assert w.worker.command.call_args.args == ("registration_preview", True)


def test_duplicate_and_cancel_messages_do_not_leave_registration_running(ui):
    w = ui.window
    w.open_registration(token=ui.token)
    dialog = w.registration_dialog
    dialog.name.setText("Duplicate")
    w.start_registration(dialog)
    w.on_registration_result({"worker_id": "current", "request_id": dialog.request_id,
                              "success": False, "message": "User already registered."})
    assert dialog.message.text() == "User already registered."
    assert not dialog.capturing and not dialog.name.isReadOnly()
    assert not w._enrolling
    w.start_registration(dialog)
    dialog.reject()
    assert any(call.args == ("cancel_enroll",) for call in w.worker.command.call_args_list)


def test_late_registration_result_cannot_complete_a_new_request(ui):
    w = ui.window
    w.open_registration(token=ui.token)
    dialog = w.registration_dialog
    dialog.name.setText("User")
    w.start_registration(dialog)
    w.on_registration_result({"worker_id": "current", "request_id": "old-request",
                              "success": True, "message": "User registered successfully."})
    assert not dialog.completed


def test_manage_users_shows_roles_and_saves_permissions_with_immediate_invalidation(ui, database):
    w = ui.window
    assert w.open_manage_users()
    dialog = w.manage_users_dialog
    dialog.selector.setCurrentIndex(dialog.selector.findData(ui.guest.user_id))
    assert dialog.role.currentData() == "user"
    assert not dialog.permission_widgets["trusted_presence"].isChecked()
    dialog.permission_widgets["can_control_word"].setChecked(False)
    dialog.active.setChecked(False)
    dialog.save_user()
    changed = database.users.get(ui.guest.user_id)
    assert not changed.is_active and not changed.permissions.can_control_word
    w.worker.invalidate.assert_called_with("User directory changed", preserve_presence=True)
    w.worker.command.assert_called_with("reload")


def test_manage_users_deletion_and_reenrollment_are_protected(ui, database, monkeypatch):
    w = ui.window
    w.open_manage_users()
    dialog = w.manage_users_dialog
    dialog.selector.setCurrentIndex(dialog.selector.findData(ui.guest.user_id))
    dialog.reenroll_user()
    assert w.registration_dialog.user.user_id == ui.guest.user_id
    assert w.registration_dialog.name.isReadOnly()
    w.registration_dialog.reject()
    w.open_manage_users()
    dialog = w.manage_users_dialog
    dialog.selector.setCurrentIndex(dialog.selector.findData(ui.guest.user_id))
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Yes)
    dialog.delete_user()
    assert database.users.get(ui.guest.user_id) is None
    assert database.db.execute("SELECT COUNT(*) FROM face_templates WHERE user_id=?", (ui.guest.user_id,)).fetchone()[0] == 0


@pytest.mark.parametrize("status, expected", [
    ({"state": "VERIFIED", "face_status": "matched", "face_count": 1}, "VERIFIED:"),
    ({"state": "UNKNOWN_PERSON", "face_status": "unknown", "face_count": 1, "user": None}, "UNREGISTERED"),
    ({"state": "TEMPORARILY_MISSING", "face_status": "absent", "face_count": 0, "user": None}, "NO USER DETECTED"),
    ({"state": "UNKNOWN_PERSON", "face_status": "uncertain", "face_count": 1, "user": None}, "VERIFYING"),
    ({"state": "CAMERA_ERROR", "face_status": "camera_error", "face_count": 0, "user": None}, "CAMERA ERROR"),
])
def test_camera_status_distinguishes_registered_unknown_absent_and_uncertain(ui, status, expected):
    from visionguard.recognition import public_user
    w = ui.window
    w.on_status({"multi_user": True, "enrolled": True, "camera": "Connected",
                 "user": public_user(ui.owner), "gesture_access": True, **status})
    assert expected in w.state_label.toolTip()


def test_unknown_gesture_denial_is_short_and_not_repeated_every_frame(ui, monkeypatch):
    w = ui.window
    present = Mock()
    monkeypatch.setattr(w.toast, "present", present)
    status = {"multi_user": True, "state": "UNKNOWN_PERSON", "face_status": "unknown", "camera": "Connected",
              "face_count": 1, "enrolled": True, "gesture_details": {"detected": ["RIGHT_L"], "confidence": [.99], "authorized": False}}
    for _ in range(10):
        w.on_status(status)
    present.assert_called_once()
    assert present.call_args.args[1] == "ACCESS DENIED — UNREGISTERED USER"


def test_multiple_people_display_pause_message(ui):
    w = ui.window
    w.on_status({"multi_user": True, "state": "TEMPORARILY_MISSING", "face_count": 2,
                 "face_status": "multiple", "enrolled": True, "camera": "Connected"})
    assert w.state_label.toolTip() == "MULTIPLE PEOPLE"
    assert w.gestures_status.text() == "GESTURES LOCKED"
    assert w.activity.text() == "MULTIPLE PEOPLE — GESTURES PAUSED"
