"""PySide6 desktop interface, protected settings, enrollment and visible tray."""
from dataclasses import asdict
from datetime import datetime
import time
import uuid
from pathlib import Path
import logging
import json
import sys
from PySide6.QtCore import Qt, QTimer, QSize
from PySide6.QtGui import QAction, QIcon
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
    QDoubleSpinBox, QFormLayout, QFrame, QHBoxLayout, QInputDialog,
    QLabel, QLineEdit, QMainWindow, QMenu, QMessageBox, QPushButton,
    QScrollArea, QSpinBox, QSystemTrayIcon, QTextEdit, QVBoxLayout, QWidget, QTabWidget,
)
from .camera_view import CameraView, ActionToast
from .config import Settings
from .runtime import VisionWorker
from .startup import set_startup
from .user_dialogs import RegistrationDialog, ManageUsersDialog
from .writing_overlay import AnnotationOverlay
from .writing_controls import CalibrationDialog

STYLE = """
QWidget { background:#0c1422; color:#d9e5f4; font-family:'Segoe UI'; font-size:13px; }
QMainWindow { background:#0c1422; }
QLabel#Title { font-size:27px; font-weight:650; }
QLabel#Sub { color:#93a9c4; }
QLabel#Brand { font-size:20px; font-weight:700; color:#68d9ef; padding:8px 0; }
QFrame#StatusBar, QFrame#ActivityBar { background:rgba(10,20,30,220); border:1px solid #253844; border-radius:12px; }
QFrame#StatusBar QLabel, QFrame#ActivityBar QLabel { background:transparent; }
QLabel#State { font-size:11px; font-weight:700; color:#ffd27b; }
QLabel#LiveBrand { color:#e9f5fc; font-size:13px; font-weight:700; letter-spacing:1px; }
QLabel#CompactStatus { color:#a7bac7; font-size:11px; font-weight:600; }
QLabel#Activity { color:#e1ecf4; font-size:13px; }
QFrame#Guide, QFrame#ActionToast { background:rgba(10,23,34,235); border:1px solid #365361; border-radius:16px; }
QFrame#Guide QLabel, QFrame#ActionToast QLabel { background:transparent; }
QLabel#ToastCaption { color:#a7bac7; font-size:11px; font-weight:600; }
QLabel#ToastTitle { font-size:23px; font-weight:700; }
QPushButton#Gear { font-size:21px; padding:0; border:0; background:transparent; }
QPushButton { background:#203952; border:1px solid #35536d; padding:10px 16px; border-radius:8px; }
QPushButton:hover { background:#2c506d; }
QPushButton#Primary { background:#146f87; border:1px solid #298fa5; color:white; font-weight:600; }
QPushButton:disabled { color:#637389; background:#172334; }
QPushButton#Primary:disabled { color:#8ca5b6; background:#1c3d4a; border:1px solid #35536d; }
QProgressBar { background:#101d2e; color:#d9e5f4; border:1px solid #33465f; border-radius:5px; text-align:center; min-height:20px; }
QProgressBar::chunk { background:#146f87; border-radius:4px; }
QLineEdit,QComboBox,QSpinBox,QDoubleSpinBox,QTextEdit { background:#101d2e; border:1px solid #33465f; border-radius:6px; padding:7px; }
QComboBox QAbstractItemView { background:#142438; selection-background-color:#2b526a; }
QCheckBox { spacing:10px; padding:5px 0; }
QCheckBox::indicator { width:17px; height:17px; }
QScrollArea { border:0; }
QMenu { background:#142438; border:1px solid #35536d; }
QMenu::item { padding:8px 22px; }
QMenu::item:selected { background:#24475f; }
QToolTip { background:#203952; color:white; border:1px solid #35536d; }
"""


def text_label(text, name=None, wrap=True):
    label = QLabel(text)
    if name:
        label.setObjectName(name)
    label.setWordWrap(wrap)
    label.setTextFormat(Qt.TextFormat.PlainText)
    return label


def button(text, callback, primary=False):
    result = QPushButton(text)
    if primary:
        result.setObjectName("Primary")
    result.clicked.connect(callback)
    return result


class SetupWizard(QDialog):
    def __init__(self, parent=None, has_passphrase=False):
        super().__init__(parent)
        self.setWindowTitle("Welcome to VisionGuard AI")
        self.setMinimumWidth(510)
        layout = QVBoxLayout(self)
        layout.setSpacing(16)
        layout.addWidget(text_label("Your workspace. Your presence.", "Title"))
        layout.addWidget(text_label(
            "VisionGuard uses your webcam locally to verify your presence and recognize hand gestures. "
            "Face embeddings are encrypted for this Windows account. Camera frames are not saved or uploaded. "
            "This supplements Windows security; use your Windows PIN, password, or Hello to unlock."))
        self.consent = QCheckBox("I allow VisionGuard AI to access this camera for local monitoring")
        layout.addWidget(self.consent)
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.confirm = QLineEdit()
        self.confirm.setEchoMode(QLineEdit.EchoMode.Password)
        self.has_passphrase = has_passphrase
        if not has_passphrase:
            layout.addWidget(text_label("Create a local administrator recovery passphrase (12+ characters). "
                                        "Keep it somewhere safe. It protects enrollment deletion, security settings, pausing and exit. "
                                        "It also authorizes registering and managing users. Use a different passphrase from your Windows password.", "Sub"))
            form = QFormLayout()
            form.addRow("Recovery passphrase", self.password)
            form.addRow("Confirm", self.confirm)
            layout.addLayout(form)
        self.error = text_label("")
        layout.addWidget(self.error)
        controls = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        controls.button(QDialogButtonBox.StandardButton.Ok).setText("Set up camera & enrollment")
        controls.accepted.connect(self.validate)
        controls.rejected.connect(self.reject)
        layout.addWidget(controls)

    def validate(self):
        if not self.consent.isChecked():
            self.error.setText("Camera consent is required to start monitoring.")
        elif not self.has_passphrase and (len(self.password.text()) < 12 or self.password.text() != self.confirm.text()):
            self.error.setText("Enter matching recovery passphrases with at least 12 characters.")
        else:
            self.accept()


class MainWindow(QMainWindow):
    def __init__(self, database, settings, *, smoke=False, background=False, force_simulation=False):
        super().__init__()
        self.database, self.settings = database, settings
        self.smoke, self.background = smoke, background
        self.hide_after_verification = False
        self.force_simulation = force_simulation
        if force_simulation:
            settings.development_simulation = True
        self.worker = None
        self.last_status = {}
        self.exiting = False
        self.events = []
        self.widgets = {}
        self.setWindowTitle("VisionGuard AI — Presence & workspace control")
        self.resize(1180, 860)
        self.setMinimumSize(800, 600)
        asset_root = Path(sys._MEIPASS) if getattr(sys, "frozen", False) else Path(__file__).resolve().parent.parent
        self.asset_root = asset_root
        self.icon = QIcon(str(asset_root / "assets/shield.svg"))
        self.setWindowIcon(self.icon)
        self.build_ui()
        self.build_tray()
        self.annotation_overlay = AnnotationOverlay(lambda: self.worker)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.heartbeat)
        self.timer.start(100)
        if not smoke:
            QTimer.singleShot(100, self.initialize)

    def build_ui(self):
        self.preview = CameraView(self)
        self.setCentralWidget(self.preview)
        self.settings_dialog = None
        self.registration_dialog = None
        self.manage_users_dialog = None
        self.calibration_dialog = None
        self.diagnostic_panel = None
        self._diagnostic_updated_at = -1e9
        self.mapping_widgets = {}
        self._blocked_key = None
        self._last_blocked_at = -1e9
        self._last_detection = ""
        self._last_camera = None
        self._counting = False
        self._enrolled = self.database.users.has_users() or bool(len(self.database.embeddings()))
        self._enrolling = False

        self.top_bar = QFrame(self.preview)
        self.top_bar.setObjectName("StatusBar")
        top = QHBoxLayout(self.top_bar)
        top.setContentsMargins(16, 8, 10, 8)
        top.setSpacing(10)
        brand = text_label("VISIONGUARD AI", "LiveBrand", False)
        brand.setFixedWidth(130)
        top.addWidget(brand)
        initial_auth = "VERIFYING" if self._enrolled else "NO USER DETECTED"
        self.state_label = text_label(initial_auth, "State", False)
        self.state_label.setProperty("full_auth", initial_auth)
        self.state_label.setToolTip(initial_auth)
        top.addWidget(self.state_label)
        self.app_label = text_label("NO OFFICE APP", "CompactStatus", False)
        self.app_label.setFixedWidth(80)
        top.addWidget(self.app_label)
        self.gestures_status = text_label("", "CompactStatus", False)
        self.gestures_status.setFixedWidth(110)
        top.addWidget(self.gestures_status)
        top.addStretch()
        self.register_button = button("+ Register User", self.begin_enrollment)
        self.register_button.setStyleSheet("font-size:11px; padding:7px 9px;")
        self.register_button.setFixedWidth(124)
        self.register_button.setAccessibleName("Register User (administrator authentication required)")
        top.addWidget(self.register_button)
        self.settings_button = button("", self.open_settings)
        self.settings_button.setIcon(QIcon(str(self.asset_root / "assets/settings.svg")))
        self.settings_button.setIconSize(QSize(21, 21))
        self.settings_button.setObjectName("Gear")
        self.settings_button.setFixedSize(34, 32)
        self.settings_button.setToolTip("Settings")
        self.settings_button.setAccessibleName("Open settings")
        top.addWidget(self.settings_button)

        self.activity_bar = QFrame(self.preview)
        self.activity_bar.setObjectName("ActivityBar")
        bottom = QHBoxLayout(self.activity_bar)
        bottom.setContentsMargins(16, 10, 16, 10)
        bottom.setSpacing(14)
        self.activity = text_label("Ready to start", "Activity")
        bottom.addWidget(self.activity, 1)
        self.mode = text_label("", "CompactStatus", False)
        bottom.addWidget(self.mode)
        self.pen_indicator = text_label("", "CompactStatus", False)
        self.pen_indicator.setPixmap(QIcon(str(self.asset_root / "assets/pen.svg")).pixmap(18, 18))
        self.pen_indicator.setAccessibleName("Writing Mode ON")
        self.pen_indicator.setToolTip("Writing Mode ON")
        self.pen_indicator.hide()
        bottom.addWidget(self.pen_indicator)

        self.guide = QFrame(self.preview)
        self.guide.setObjectName("Guide")
        guide_layout = QVBoxLayout(self.guide)
        guide_layout.setContentsMargins(20, 16, 20, 16)
        guide_layout.setSpacing(12)
        self.prompt = text_label("Enroll your face to get started")
        self.prompt.setAlignment(Qt.AlignmentFlag.AlignCenter)
        guide_layout.addWidget(self.prompt)
        self.enroll_button = button("Register Administrator", self.begin_enrollment, True)
        guide_layout.addWidget(self.enroll_button)
        self.cancel_enrollment_button = button("Cancel enrollment", self.cancel_enrollment)
        guide_layout.addWidget(self.cancel_enrollment_button)
        self.cancel_enrollment_button.hide()
        self.toast = ActionToast(self.preview)
        self.update_mode()
        self.update_guide()
        self.position_overlays()

    def position_overlays(self):
        if not hasattr(self, "top_bar"):
            return
        width, height = self.preview.width(), self.preview.height()
        self.state_label.setFixedWidth(max(100, min(230, width - 700)))
        full_auth = self.state_label.property("full_auth")
        if full_auth:
            self.state_label.setText(self.state_label.fontMetrics().elidedText(full_auth, Qt.TextElideMode.ElideRight, self.state_label.width()))
        margin = 14
        self.top_bar.setGeometry(margin, margin, max(1, width - 2 * margin), 54)
        self.activity_bar.setGeometry(margin, max(74, height - 58), max(1, width - 2 * margin), 44)
        self.guide.setFixedWidth(min(440, max(260, width - 64)))
        self.guide.adjustSize()
        guide_y = (height - self.guide.height()) // 2 if not self._enrolled or self._enrolling else height - self.guide.height() - 80
        self.guide.move((width - self.guide.width()) // 2, max(82, guide_y))
        self.toast.move((width - self.toast.width()) // 2,
                        max(82, int(height * .68) - self.toast.height() // 2))

    def update_guide(self):
        self.enroll_button.setVisible(not self._enrolled and not self._enrolling)
        self.cancel_enrollment_button.setVisible(self._enrolling)
        state = self.last_status.get("state", "")
        needs_challenge = self.last_status.get("face_status") in {"challenge", "quality", "multiple"}
        self.guide.setVisible(not self._enrolled or self._enrolling or (needs_challenge and state != "VERIFIED"))
        if self.registration_dialog is not None:
            self.guide.hide()
        if not self._enrolled and not self._enrolling:
            self.prompt.setText("Register the device administrator to get started")
        self.position_overlays()

    def check(self, key, label, form):
        widget = QCheckBox(label)
        widget.setChecked(getattr(self.settings, key))
        form.addRow(widget)
        self.widgets[key] = widget

    def number(self, key, label, form, lo, hi, step=.1, integer=False):
        widget = QSpinBox() if integer else QDoubleSpinBox()
        widget.setRange(lo, hi)
        widget.setSingleStep(1 if integer else step)
        widget.setValue(getattr(self.settings, key))
        form.addRow(label, widget)
        self.widgets[key] = widget

    def choice(self, key, label, values, form):
        widget = QComboBox()
        widget.addItems(values)
        widget.setCurrentText(getattr(self.settings, key))
        form.addRow(label, widget)
        self.widgets[key] = widget

    def open_settings(self):
        if self.settings_dialog is not None:
            self.settings_dialog.raise_()
            return
        dialog = QDialog(self)
        self.settings_dialog = dialog
        dialog.setWindowTitle("VisionGuard · Settings")
        dialog.resize(490, 640)
        dialog.setMinimumWidth(450)
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.addWidget(text_label("Settings", "Title"))
        layout.addWidget(text_label("Protected changes require your recovery passphrase.", "Sub"))
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        body = QVBoxLayout(content)
        body.setContentsMargins(0, 4, 12, 4)
        form = QFormLayout()
        form.setVerticalSpacing(10)
        self.widgets = {}
        self.mapping_widgets = {}
        self.check("auto_lock", "Enable auto-lock", form)
        self.number("absence_timeout", "Absence timeout (seconds)", form, 2, 120, .5)
        self.check("gestures_enabled", "Enable gesture control", form)
        self.number("camera_index", "Camera (0 = built-in)", form, 0, 15, integer=True)
        self.number("gesture_threshold", "Gesture sensitivity", form, .6, .99, .01)
        self.check("start_with_windows", "Start with Windows", form)
        self.check("minimize_to_tray", "Minimize to tray on close", form)
        self.check("development_simulation", "Simulate Windows locking", form)
        self.widgets["development_simulation"].setEnabled(not self.force_simulation)
        if self.force_simulation:
            self.widgets["development_simulation"].setToolTip("Forced by --simulation for this launch")
        body.addLayout(form)
        body.addWidget(button("Manage Users…", self.open_manage_users))
        more = QPushButton("More options")
        more.setCheckable(True)
        body.addWidget(more)
        advanced = QWidget()
        extra = QFormLayout(advanced)
        extra.setContentsMargins(0, 8, 0, 8)
        self.check("swap_hands", "Swap left/right labels", extra)
        self.number("gesture_cooldown", "Gesture cooldown (seconds)", extra, .3, 5, .1)
        self.number("gesture_frames", "Consecutive gesture frames", extra, 3, 15, integer=True)
        self.number("pinch_ratio", "Pinch distance / palm size", extra, .1, .4, .01)
        self.number("recognition_threshold", "Face threshold", extra, .4, .9, .01)
        self.number("recognition_hysteresis", "Retention hysteresis", extra, 0, .08, .01)
        self.number("verification_grace", "Verification grace (seconds)", extra, 0, 3, .1)
        self.check("strict_privacy", "Strict privacy with extra faces", extra)
        self.choice("camera_policy", "Camera failure policy", ["lock", "alert"], extra)
        self.check("preview_visible", "Show preview", extra)
        self.check("overlays", "Show detection overlays", extra)
        self.check("launch_background", "Launch in background after verification", extra)
        self.check("notifications", "Important tray notifications", extra)
        self.check("developer_diagnostics", "Show diagnostic details here", extra)
        self.diagnostic_panel = QTextEdit()
        self.diagnostic_panel.setReadOnly(True)
        self.diagnostic_panel.setFixedHeight(180)
        self.diagnostic_panel.setVisible(self.settings.developer_diagnostics)
        self.widgets["developer_diagnostics"].toggled.connect(self.diagnostic_panel.setVisible)
        extra.addRow(self.diagnostic_panel)
        for app, actions in [("PowerPoint", ["previous", "next", "exit", "none"]),
                             ("Word", ["previous", "next", "pause", "toggle", "none"])]:
            for gesture in ["LEFT_L", "RIGHT_L", "FIST", "PINCH"]:
                widget = QComboBox()
                widget.addItems(actions)
                widget.setCurrentText(self.settings.mappings[app][gesture])
                extra.addRow(f"{app} · {gesture.replace('_', ' ')}", widget)
                self.mapping_widgets[(app, gesture)] = widget
        for title, callback in [("Change administrator recovery passphrase…", self.change_passphrase),
                                ("Revoke camera consent…", self.revoke_camera),
                                ("Restart monitoring…", self.restart_worker)]:
            extra.addRow(button(title, callback))
        advanced.hide()
        more.toggled.connect(advanced.setVisible)
        more.toggled.connect(lambda checked: more.setText("Fewer options" if checked else "More options"))
        body.addWidget(advanced)
        body.addStretch()
        scroll.setWidget(content)
        tabs = QTabWidget()
        tabs.setTabPosition(QTabWidget.TabPosition.West)
        tabs.addTab(scroll, "General")
        writing_scroll = QScrollArea()
        writing_scroll.setWidgetResizable(True)
        writing_page = QWidget()
        writing_layout = QVBoxLayout(writing_page)
        writing_layout.addWidget(text_label("Air Writing", "Title"))
        writing_layout.addWidget(text_label("Three fingers: toggle · Index: draw · Open palm: clear\n"
            "Show two fingers (index + middle) to rearm between commands.\n"
            "Focus a PowerPoint slideshow. Writing pauses on face verification loss.", "Sub"))
        writing_form = QFormLayout()
        self.check("writing_enabled", "Enable Air Writing", writing_form)
        self.choice("writing_color", "Pen color", ["#ff3030", "#ffff00", "#00d9ff", "#ffffff", "#181818"], writing_form)
        self.widgets["writing_color"].setEditable(True)
        self.widgets["writing_color"].setCurrentText(self.settings.writing_color)
        self.number("writing_width", "Pen thickness", writing_form, 1, 20, 1)
        self.number("writing_smoothing", "Smoothing (0–1)", writing_form, 0, 1, .05)
        self.number("writing_confidence", "Hand confidence", writing_form, .8, .99, .01)
        self.number("writing_hold", "Activation hold (seconds)", writing_form, .5, 2, .1)
        self.check("writing_indicator", "Show writing indicator", writing_form)
        writing_layout.addLayout(writing_form)
        self.writing_calibration_draft = [list(p) for p in self.settings.writing_calibration]
        writing_layout.addWidget(button("Calibrate comfortable reach…", self.calibrate_writing))
        self.calibration_status = text_label("Four-corner calibration · mirrored camera", "Sub")
        writing_layout.addWidget(self.calibration_status)
        writing_layout.addWidget(button("Clear current slide", lambda: self.clear_ink(False)))
        writing_layout.addWidget(button("Clear all temporary annotations", lambda: self.clear_ink(True)))
        writing_layout.addWidget(text_label("Clear requires a verified user and an active slideshow.\n"
            "Ink is temporary and discarded when its slideshow ends. PowerPoint content stays unchanged.", "Sub"))
        writing_layout.addStretch()
        writing_scroll.setWidget(writing_page)
        tabs.addTab(writing_scroll, "Air Writing")
        tabs.currentChanged.connect(lambda index: dialog.resize(540 if index else 490,
            min(800 if index else 640, dialog.screen().availableGeometry().height() - 60)))
        layout.addWidget(tabs, 1)
        footer = QHBoxLayout()
        footer.addWidget(button("Pause / resume…", self.toggle_pause))
        footer.addWidget(button("Exit…", self.exit_application))
        footer.addStretch()
        layout.addLayout(footer)
        controls = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        controls.accepted.connect(lambda: dialog.accept() if self.save_settings() else None)
        controls.rejected.connect(dialog.reject)
        layout.addWidget(controls)
        dialog.finished.connect(self.settings_closed)
        # Modeless: the camera remains live; authorization dialogs are still modal.
        dialog.show()

    def settings_closed(self, result):
        if self.calibration_dialog:
            self.calibration_dialog.reject()
        dialog, self.settings_dialog = self.settings_dialog, None
        self.widgets = {}
        self.mapping_widgets = {}
        self.diagnostic_panel = None
        if dialog:
            dialog.deleteLater()

    def calibrate_writing(self):
        if not self.worker or not self.worker.writing_permit():
            self.error("Verify your face and enable gesture control before calibrating.")
            return
        if self.calibration_dialog:
            self.calibration_dialog.raise_()
            return
        dialog = CalibrationDialog(self.worker, self.settings_dialog)
        self.calibration_dialog = dialog

        def finished(result):
            if result == QDialog.DialogCode.Accepted:
                self.writing_calibration_draft = dialog.corners
                self.calibration_status.setText("Calibration ready · Save settings to apply")
            self.calibration_dialog = None
            dialog.deleteLater()

        dialog.finished.connect(finished)
        dialog.show()

    def clear_ink(self, all_slides=False):
        if not self.worker or not self.worker.request_ink_clear(all_slides):
            self.error("Verify your face and select a PowerPoint slideshow before clearing ink.")

    def settings_enrollment(self):
        if self.open_manage_users() and self.settings_dialog:
            self.settings_dialog.reject()

    def cancel_enrollment(self):
        if self.worker:
            self.worker.command("cancel_enroll")
        self._enrolling = False
        self.update_guide()
        self.add_event("Enrollment cancelled")

    def build_tray(self):
        self.tray = QSystemTrayIcon(self.icon, self)
        self.tray.setToolTip("VisionGuard AI · setup required")
        menu = QMenu(self)
        for title, callback in [
            ("Open camera", self.open_camera),
            ("Enable / disable gestures", self.toggle_gestures),
            ("Pause / resume monitoring (protected)", self.toggle_pause),
            ("Settings", self.open_settings),
            ("Register User (administrator)", self.begin_enrollment),
            ("Manage Users (administrator)", self.open_manage_users),
            ("Exit application (protected)", self.exit_application),
        ]:
            action = QAction(title, self)
            action.triggered.connect(callback)
            menu.addAction(action)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(lambda reason: self.open_camera() if reason == QSystemTrayIcon.ActivationReason.Trigger else None)
        if not self.smoke:
            self.tray.show()

    def initialize(self):
        try:
            has_pass = self.database.get("authorization") is not None
            if not self.settings.camera_consent or not has_pass:
                self.show()
                wizard = SetupWizard(self, has_pass)
                if wizard.exec() != QDialog.DialogCode.Accepted:
                    self.add_event("Camera consent not granted; monitoring remains off")
                    return
                if not has_pass:
                    self.database.set_passphrase(wizard.password.text())
                    self._setup_token = self.database.users.authenticate_admin(wizard.password.text())
                    wizard.password.clear()
                    wizard.confirm.clear()
                self.settings.camera_consent = True
                self.database.save_settings(self.settings)
            self.restart_worker()
            if not self.database.users.has_users() and not len(self.database.embeddings()):
                self._enrolled = False
                self.update_guide()
                self.open_registration(token=getattr(self, "_setup_token", None))
                self._setup_token = None
            elif self.background or self.settings.launch_background:
                self.hide_after_verification = True
                self.add_event("Background launch: complete verification; window then minimizes to tray")
        except Exception as exc:
            self.error(str(exc))

    def authorize(self) -> bool:
        if self.smoke:
            return False
        if self.database.get("authorization") is None:
            self.error("Complete first-launch setup to create an application recovery passphrase.")
            return False
        value, ok = QInputDialog.getText(self, "Authorize protected action",
                                        "Application recovery passphrase (not your Windows password):",
                                        QLineEdit.EchoMode.Password)
        if not ok:
            return False
        try:
            valid = self.database.authorize(value)
        except Exception:
            self.error("Protected authorization data could not be read. Restore your profile backup.")
            return False
        value = ""
        if not valid:
            self.error("Authorization failed or temporarily rate limited. Wait and retry.")
        return valid

    def restart_worker(self):
        if self.smoke:
            return
        if not self.settings.camera_consent:
            self.initialize()
            return
        # Restarting may reset an active absence timer, so it is protected.
        if self.worker is not None:
            if not self.authorize():
                return
            if not self.worker.stop():
                self.error("A worker is blocked. Monitoring watchdog remains active. Restart after checking the camera driver.")
                return
        self.worker = VisionWorker(self.settings, self.database, self.settings.development_simulation)
        # Pull the current snapshot and latest preview in heartbeat. Old queued
        # signals from a replaced worker cannot overwrite this worker's state.
        self.worker.event.connect(self.add_event)
        self.worker.command_result.connect(self.on_command_result)
        self.worker.enrollment_done.connect(self.enrollment_finished)
        self.worker.registration_result.connect(self.on_registration_result)
        self.worker.start()
        self.add_event("Monitoring started")

    def begin_enrollment(self):
        return self.open_registration()

    def request_admin_session(self):
        if self.smoke:
            return None
        value, ok = QInputDialog.getText(self, "Administrator authentication",
            "Local administrator recovery passphrase (not your Windows password):", QLineEdit.EchoMode.Password)
        if not ok:
            return None
        try:
            return self.database.users.authenticate_admin(value)
        except (PermissionError, RuntimeError) as exc:
            self.error(str(exc))
            return None
        finally:
            value = ""

    def open_registration(self, *, token=None, user=None):
        if self.registration_dialog:
            self.registration_dialog.raise_()
            return True
        if not self.settings.camera_consent and not self.smoke:
            self.initialize()
            return self.registration_dialog is not None
        token = token or self.request_admin_session()
        if not token:
            return False
        try:
            self.database.users.require_admin(token)
        except PermissionError as exc:
            self.error(str(exc))
            return False
        if not self.worker or not self.worker.isRunning():
            self.restart_worker()
        if not self.smoke and (not self.worker or not self.worker.isRunning()):
            self.error("Camera is unavailable. Check camera consent and restart monitoring in Settings.")
            return False
        dialog = RegistrationDialog(self, token, user)
        self.registration_dialog = dialog
        dialog.finished.connect(self.registration_closed)
        if self.worker:
            self.worker.command("registration_preview", True)
        dialog.update_status(self.last_status)
        dialog.show()
        return True

    def start_registration(self, dialog):
        if dialog is not self.registration_dialog or not self.worker:
            return False
        try:
            self.database.users.require_admin(dialog.token)
            name = self.database.users._name(dialog.name.text())
        except (ValueError, PermissionError) as exc:
            dialog.message.setText(str(exc))
            return False
        dialog.request_id = uuid.uuid4().hex
        self.worker.command("register", {"token": dialog.token, "name": name, "request_id": dialog.request_id,
                            "user_id": dialog.user.user_id if dialog.user else None})
        dialog.capturing = True
        dialog.capture.setText("Registering…")
        dialog.capture.setEnabled(False)
        dialog.name.setReadOnly(True)
        dialog.message.setText("")
        self._enrolling = True
        dialog.prompt.setText("Look at the camera. Follow the head-turn prompts.")
        self.update_guide()
        self.add_event("Face registration started")
        return True

    def registration_closed(self, result):
        dialog, self.registration_dialog = self.registration_dialog, None
        if self.worker:
            if dialog and dialog.capturing and not dialog.completed:
                self.worker.command("cancel_enroll")
            self.worker.command("registration_preview", False)
        self._enrolling = False
        self.update_guide()
        if dialog:
            dialog.deleteLater()

    def on_registration_result(self, feedback):
        if self.worker is None or feedback.get("worker_id") != self.worker.worker_id:
            return
        if self.registration_dialog:
            if feedback.get("request_id") != self.registration_dialog.request_id:
                return
            self.registration_dialog.result_feedback(feedback)
        self._enrolling = False
        self._enrolled = self.database.users.has_users()
        self.update_guide()
        if feedback.get("success"):
            self.toast.present("REGISTRATION", "User registered successfully.", True)
        self.add_event(feedback.get("message", "Registration stopped"))

    def open_manage_users(self):
        if self.manage_users_dialog:
            self.manage_users_dialog.raise_()
            return True
        token = self.request_admin_session()
        if not token:
            return False
        try:
            self.database.users.require_admin(token)
        except PermissionError as exc:
            self.error(str(exc))
            return False
        self.manage_users_dialog = ManageUsersDialog(self, token)
        self.manage_users_dialog.finished.connect(self.manage_users_closed)
        self.manage_users_dialog.show()
        return True

    def manage_users_closed(self, result):
        dialog, self.manage_users_dialog = self.manage_users_dialog, None
        if dialog:
            dialog.deleteLater()

    def directory_changed(self):
        if self.worker:
            self.worker.invalidate("User directory changed", preserve_presence=True)
            self.worker.command("reload")
        self._enrolled = self.database.users.has_users()
        self.update_guide()
        self.add_event("User directory updated")

    def enrollment_finished(self):
        if self.sender() is not None and self.sender() is not self.worker:
            return
        self._enrolled, self._enrolling = True, False
        self.update_guide()
        if self.database.users.has_users():
            return
        self.toast.present("FACE ENROLLMENT", "Enrollment complete", True)
        self.add_event("Face enrolled · complete verification")

    def delete_enrollment(self):
        if self.database.users.has_users():
            self.open_manage_users()
            return
        if not self.authorize():
            return
        self.database.delete_enrollment()
        if self.worker:
            self.worker.invalidate("Enrollment deleted")
            self.worker.enrolled = False
            self.worker.command("cancel_enroll")
            self.worker.command("reload")
        self._enrolled, self._enrolling = False, False
        self.update_guide()
        self.add_event("Biometric enrollment deleted")

    def change_passphrase(self):
        if not self.authorize():
            return
        value, ok = QInputDialog.getText(self, "New recovery passphrase", "New application passphrase (12+ characters):", QLineEdit.EchoMode.Password)
        if ok:
            confirm, accepted = QInputDialog.getText(self, "Confirm passphrase", "Repeat the new passphrase:", QLineEdit.EchoMode.Password)
            if accepted and value == confirm:
                try:
                    self.database.set_passphrase(value)
                    self.add_event("Recovery passphrase changed")
                except ValueError as exc:
                    self.error(str(exc))
            else:
                self.error("Passphrases did not match")

    def save_settings(self):
        candidate = asdict(self.settings)
        if hasattr(self, "writing_calibration_draft"):
            candidate["writing_calibration"] = self.writing_calibration_draft
        for key, widget in self.widgets.items():
            candidate[key] = widget.isChecked() if isinstance(widget, QCheckBox) else widget.currentText() if isinstance(widget, QComboBox) else widget.value()
        for (app, gesture), widget in self.mapping_widgets.items():
            candidate["mappings"][app][gesture] = widget.currentText()
        if self.force_simulation:
            candidate["development_simulation"] = True
        try:
            settings = Settings(**candidate).validate()
            if not self.authorize():
                return False
            if settings.auto_lock and not settings.development_simulation:
                if QMessageBox.question(self, "Enable real Windows locking",
                    "VisionGuard will call LockWorkStation when owner verification is missing for the configured timeout. "
                    "Keep your Windows PIN/password ready. Enable real locking now?") != QMessageBox.StandardButton.Yes:
                    return False
            if settings.start_with_windows != self.settings.start_with_windows:
                set_startup(settings.start_with_windows)
            self.database.save_settings(settings)
            self.settings = settings
            if self.worker:
                self.worker.invalidate("Settings changed")
                self.worker.command("settings", settings)
            if not settings.preview_visible:
                self.preview.setText("Preview hidden · monitoring continues")
            self.update_mode()
            self.add_event("Settings saved")
            return True
        except Exception as exc:
            self.error(str(exc))
            return False

    def toggle_preview(self):
        self.settings.preview_visible = not self.settings.preview_visible
        if "preview_visible" in self.widgets:
            self.widgets["preview_visible"].setChecked(self.settings.preview_visible)
        if not self.settings.preview_visible:
            self.preview.clear()
            self.preview.setText("Preview hidden\nMonitoring continues")
        if self.worker:
            self.worker.command("settings", self.settings)

    def toggle_gestures(self):
        self.settings.gestures_enabled = not self.settings.gestures_enabled
        if "gestures_enabled" in self.widgets:
            self.widgets["gestures_enabled"].setChecked(self.settings.gestures_enabled)
        self.update_mode()
        if self.worker:
            self.worker.command("settings", self.settings)
        self.add_event("Gestures enabled" if self.settings.gestures_enabled else "Gestures disabled")

    def toggle_pause(self):
        if self.worker and self.authorize():
            self.worker.invalidate("Pause/resume requested")
            self.worker.command("pause", not self.worker.paused)
            self.add_event("Monitoring pause/resume authorized")

    def revoke_camera(self):
        if not self.authorize():
            return
        if self.worker and not self.worker.stop():
            self.error("Camera driver did not stop. Consent remains enabled; close the driver before retrying.")
            return
        self.worker = None
        self.settings.camera_consent = False
        self.database.save_settings(self.settings)
        self.preview.clear()
        self.preview.setText("Camera consent revoked")
        self.on_status({"state": "PAUSED", "camera": "Consent revoked", "controls": "Suspended", "presence": "Unverified"})
        self.add_event("Camera consent revoked; monitoring stopped")

    def update_mode(self):
        if not self.settings.auto_lock:
            message = "AUTO-LOCK OFF"
        elif self.settings.development_simulation:
            message = "LOCK SIMULATION"
        else:
            message = "AUTO-LOCK ON"
        self.mode.setText(message)
        self.gestures_status.setText("GESTURES ON" if self.settings.gestures_enabled else "GESTURES OFF")

    def on_image(self, image):
        if self.last_status.get("camera", "Connected") == "Connected":
            if self.settings.preview_visible:
                self.preview.set_image(image)
            if self.registration_dialog:
                self.registration_dialog.preview.set_image(image)
            if self.calibration_dialog:
                self.calibration_dialog.preview.set_image(image)

    def on_status(self, values):
        if "worker_id" in values:
            if self.worker is None or values["worker_id"] != self.worker.worker_id:
                return
            if (self.last_status.get("worker_id") == values["worker_id"]
                    and values.get("revision", 0) < self.last_status.get("revision", -1)):
                return
            if self.last_status.get("worker_id") != values["worker_id"]:
                self.last_status = {}
        previous = self.last_status.copy()
        self.last_status.update(values)
        status = self.last_status
        self.pen_indicator.setVisible(self.settings.writing_indicator and status.get("writing_mode", False))
        if (self.diagnostic_panel is not None and self.diagnostic_panel.isVisible()
                and "diagnostics" in status and time.monotonic() - self._diagnostic_updated_at >= .5):
            self.diagnostic_panel.setPlainText(json.dumps(status["diagnostics"], indent=2, ensure_ascii=False))
            self._diagnostic_updated_at = time.monotonic()
        state = status.get("state", "UNENROLLED")
        face_status = status.get("face_status", "")
        auth = ("VERIFIED" if state == "VERIFIED" else "UNKNOWN" if face_status == "unknown" or state == "UNKNOWN_PERSON"
                else "VERIFYING" if face_status in {"challenge", "quality", "matched", "enrollment"} or state == "VERIFYING"
                else "USER ABSENT")
        if status.get("multi_user"):
            user = status.get("user")
            auth = ("MULTIPLE PEOPLE" if status.get("face_count", 0) > 1 else
                    state.replace("_", " ") if state in {"CAMERA_ERROR", "RECOGNITION_ERROR", "PAUSED", "LOCKED"} else
                    "TEMPORARILY MISSING" if status.get("face_count", 0) == 0 and status.get("identity_in_grace") else
                    "NO USER DETECTED" if status.get("face_count", 0) == 0 else
                    "VERIFYING" if face_status in {"challenge", "quality", "matched", "uncertain", "enrollment"} else
                    "UNREGISTERED" if state == "UNKNOWN_PERSON" or face_status == "unknown" else "VERIFYING")
            if state == "VERIFIED" and user and status.get("face_count") == 1:
                auth = f"VERIFIED: {user['user_name']}"
            self.state_label.setToolTip(auth)
            self.state_label.setProperty("full_auth", auth)
            auth = self.state_label.fontMetrics().elidedText(auth, Qt.TextElideMode.ElideRight,
                self.state_label.width())
        else:
            self.state_label.setProperty("full_auth", None)
        self.state_label.setText(auth)
        color = "#75edb8" if state == "VERIFIED" else "#ff8686" if auth in {"UNKNOWN", "UNREGISTERED"} else "#ffd27b"
        if self.state_label.property("auth_color") != color:
            self.state_label.setProperty("auth_color", color)
            self.state_label.setStyleSheet(f"color:{color};")
        if not status.get("multi_user"):
            self.state_label.setToolTip(state.replace("_", " "))
        self.tray.setToolTip(f"VisionGuard AI · {state}")
        if state == "VERIFIED" and self.hide_after_verification:
            self.hide_after_verification = False
            self.hide()
        target = status.get("target", "None")
        self.app_label.setText(target.upper() if target in {"PowerPoint", "Word"} else "NO OFFICE APP")
        if "enrolled" in values:
            self._enrolled = values["enrolled"]
        if "enrolling" in values:
            self._enrolling = values["enrolling"]
        if "prompt" in values:
            prompt = values["prompt"]
            if face_status == "unknown":
                prompt = "Unknown face · look at the camera to verify"
            self.prompt.setText(prompt)
        self.update_guide()
        if self.registration_dialog:
            self.registration_dialog.update_status(status)
        camera = status.get("camera", "Not started")
        if camera != "Connected":
            self.preview.setText("Preview hidden · monitoring continues" if not self.settings.preview_visible else camera)
        if camera != self._last_camera:
            if camera == "Connected":
                self.add_event("Camera connected")
            elif self._last_camera == "Connected":
                self.add_event("Camera disconnected" if state not in {"PAUSED", "LOCKED"} else state.replace("_", " ").capitalize())
            elif camera not in {"Not started", "Waiting for camera"}:
                self.add_event(camera)
            self._last_camera = camera
        if previous.get("state") != state:
            event = {"VERIFIED": "Face verified", "UNKNOWN_PERSON": "Unknown face detected", "TEMPORARILY_MISSING": "Authorized user absent",
                     "LOCKED": "Windows locked", "PAUSED": "Monitoring paused", "RECOGNITION_ERROR": "Verification unavailable"}.get(state)
            if state == "TEMPORARILY_MISSING" and face_status in {"challenge", "quality", "matched", "enrollment"}:
                event = None
            if event:
                self.add_event(event)
        counting = status.get("remaining") is not None and not status.get("presence_authorized", state == "VERIFIED")
        if counting and not self._counting:
            self.add_event("Lock countdown started")
        self._counting = counting
        if counting:
            self.mode.setText(f"{'SIMULATED LOCK' if self.settings.development_simulation else 'LOCK'} IN {status['remaining']:.1f}s")
        else:
            self.update_mode()
        self.gestures_status.setText("GESTURES ON" if status.get("gestures_enabled", self.settings.gestures_enabled) else "GESTURES OFF")
        if (status.get("multi_user") and not status.get("gesture_access", False)
                and status.get("gestures_enabled", self.settings.gestures_enabled)):
            self.gestures_status.setText("GESTURES LOCKED")
        if status.get("multi_user") and status.get("face_count", 0) > 1:
            self.activity.setText("MULTIPLE PEOPLE — GESTURES PAUSED")
        self.detection_feedback(status)

    def detection_feedback(self, status):
        if status.get("enrolling"):
            return
        details = status.get("gesture_details", {})
        names = details.get("detected", [])
        confidences = details.get("confidence", [])
        active = [name for name, confidence in zip(names, confidences)
                  if name in {"LEFT_L", "RIGHT_L", "FIST", "PINCH"} and confidence >= self.settings.gesture_threshold]
        detected = ", ".join(active)
        if detected and detected != self._last_detection:
            self.add_event(f"{detected.replace('_', ' ').title()} detected")
        self._last_detection = detected
        if not active:
            self._blocked_key = None
            return
        reason = ""
        if not status.get("gestures_enabled", self.settings.gestures_enabled):
            reason = "GESTURE CONTROL DISABLED"
        elif not details.get("authorized", False):
            reason = ("MULTIPLE PEOPLE — GESTURES PAUSED" if status.get("face_count", 0) > 1 else
                      "ACCESS DENIED — UNREGISTERED USER" if status.get("multi_user") and status.get("face_status") == "unknown" else
                      "APPLICATION PERMISSION DENIED" if status.get("multi_user") and status.get("state") == "VERIFIED" else "USER NOT VERIFIED")
        elif not status.get("target_ready", False):
            office = status.get("office", "").lower()
            reason = "OFFICE CONNECTION ERROR" if "unavailable" in office else "NO ACTIVE SLIDESHOW" if status.get("target") == "PowerPoint" else "FOCUS AN OFFICE WINDOW"
        key = (detected, reason)
        if reason and key != self._blocked_key and time.monotonic() - self._last_blocked_at >= 2:
            self.toast.present(f"{detected.replace('_', ' ')} DETECTED", reason)
            self.add_event(reason.capitalize())
            self._last_blocked_at = time.monotonic()
        self._blocked_key = key

    def on_command_result(self, feedback):
        if self.worker is None or feedback.get("worker_id") != self.worker.worker_id:
            return
        result = feedback.get("result", "")
        success = feedback.get("success", False)
        labels = {"PowerPoint: next": "NEXT SLIDE", "PowerPoint: previous": "PREVIOUS SLIDE",
                  "Exited slideshow; presentation remains open": "SLIDESHOW EXITED",
                  "Word: next page (cursor preserved)": "NEXT PAGE", "Word: previous page (cursor preserved)": "PREVIOUS PAGE",
                  "Word Read Mode: one screen (layout-dependent)": "DOCUMENT SCROLLED",
                  "Word gestures paused": "WORD GESTURES PAUSED", "Word gestures resumed": "WORD GESTURES RESUMED"}
        if success:
            title = labels.get(result, "ACTION COMPLETED")
        else:
            lower = result.lower()
            title = ("OFFICE CONNECTION ERROR" if any(x in lower for x in ("failed", "com unavailable")) else
                     "USER NOT VERIFIED" if any(x in lower for x in ("unverified", "authentication", "verification", "owner")) else
                     "GESTURE CONTROL DISABLED" if "disabled" in lower else
                     "NO ACTIVE SLIDESHOW" if "no foreground" in lower or "no ready" in lower else
                     "ACTION CANCELLED" if "cancelled" in lower or "stale" in lower else result)
        caption = feedback.get("gesture", "").replace("_", " ")
        self.toast.present(f"{caption} DETECTED" if caption else "OFFICE", title, success)
        activity = {"NEXT SLIDE": "Next slide executed", "PREVIOUS SLIDE": "Previous slide executed",
                    "NEXT PAGE": "Next page executed", "PREVIOUS PAGE": "Previous page executed"}.get(title, title.capitalize())
        self.add_event(activity)

    def heartbeat(self):
        if self.worker:
            self.worker.watchdog()
            self.on_status(self.worker.view())
            preview = self.worker.take_image()
            if preview is not None:
                self.on_image(preview)

    def add_event(self, text):
        if text in {"Writing Mode ON", "Writing Mode OFF", "Annotations Cleared"}:
            self.toast.present("AIR WRITING", text, True)
        logging.getLogger("visionguard.activity").info(text)
        self.events.append(f"{datetime.now():%H:%M:%S}  {text}")
        self.events = self.events[-5:]
        self.activity.setText(text)
        self.activity.setToolTip("\n".join(reversed(self.events)))
        if self.settings.notifications and self.tray.isVisible() and any(x in text.lower() for x in ["lock requested", "stopped", "failed", "camera_error"]):
            self.tray.showMessage("VisionGuard AI", text, QSystemTrayIcon.MessageIcon.Warning, 5000)

    def error(self, text):
        self.add_event(text)
        QMessageBox.warning(self, "VisionGuard AI", text)

    def open_camera(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def exit_application(self):
        if self.database.get("authorization") is not None and not self.authorize():
            return
        if self.worker and not self.worker.stop():
            self.error("A worker did not stop safely. Close the camera or Office dialog and retry.")
            return
        self.exiting = True
        self.tray.hide()
        self.timer.stop()
        self.database.close()
        QApplication.quit()

    def closeEvent(self, event):
        if self.exiting or self.smoke:
            self.annotation_overlay.shutdown()
            event.accept()
        elif self.settings.minimize_to_tray and self.tray.isVisible():
            self.hide()
            event.ignore()
        else:
            event.ignore()
            self.exit_application()
