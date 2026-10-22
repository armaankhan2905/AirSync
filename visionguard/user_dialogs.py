"""Compact registration and administrator user management over the live camera."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QLabel, QLineEdit,
    QPushButton, QCheckBox, QComboBox, QProgressBar, QMessageBox, QDialogButtonBox)
from .camera_view import CameraView
from .users import Permissions


def label(text=""):
    widget = QLabel(text)
    widget.setTextFormat(Qt.TextFormat.PlainText)
    widget.setWordWrap(True)
    return widget


class RegistrationDialog(QDialog):
    def __init__(self, parent, token, user=None):
        super().__init__(parent)
        self.owner_window, self.token, self.user = parent, token, user
        self.capturing = self.completed = False
        self.request_id = None
        self.setWindowTitle("Re-enroll User" if user else "Register User")
        self.resize(540, 620)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(12)
        form = QFormLayout()
        self.name = QLineEdit(user.user_name if user else "")
        self.name.setMaxLength(60)
        self.name.setPlaceholderText("Administrator name" if not parent.database.users.has_users() else "User name")
        self.name.setReadOnly(user is not None)
        form.addRow("Name", self.name)
        layout.addLayout(form)
        self.preview = CameraView(self)
        self.preview.setMinimumSize(360, 240)
        layout.addWidget(self.preview, 1)
        self.face_indicator = label("Waiting for the camera")
        layout.addWidget(self.face_indicator)
        self.prompt = label("Look at the camera, then select Capture and Register.")
        layout.addWidget(self.prompt)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setFormat("Registration: %p%")
        layout.addWidget(self.progress)
        self.message = label()
        layout.addWidget(self.message)
        row = QHBoxLayout()
        self.capture = QPushButton("Capture and Register")
        self.capture.setObjectName("Primary")
        self.capture.setEnabled(False)
        self.capture.clicked.connect(lambda: parent.start_registration(self))
        row.addWidget(self.capture)
        self.cancel = QPushButton("Cancel")
        self.cancel.clicked.connect(self.accept_or_cancel)
        row.addWidget(self.cancel)
        layout.addLayout(row)

    def accept_or_cancel(self):
        self.accept() if self.completed else self.reject()

    def update_status(self, status):
        if self.completed:
            return
        count = status.get("face_count", 0)
        camera_ok = status.get("camera") == "Connected"
        self.face_indicator.setText("One face detected" if count == 1 else "Exactly one face must be visible" if count > 1 else "No face detected")
        if not camera_ok:
            self.face_indicator.setText(status.get("camera", "Waiting for camera"))
        self.capture.setEnabled(camera_ok and count == 1 and not self.capturing)
        if self.capturing:
            self.prompt.setText(status.get("prompt", "Follow the head-turn prompts"))
            samples, poses = status.get("registration_samples", 0), status.get("registration_poses", 0)
            self.progress.setValue(int(min(samples / 6, 1) * 60 + min(poses / 3, 1) * 35))

    def result_feedback(self, feedback):
        self.capturing = False
        self.completed = feedback.get("success", False)
        self.message.setText(feedback.get("message", "Registration stopped"))
        self.message.setStyleSheet("color:#75edb8;" if self.completed else "color:#ffd27b;")
        self.capture.setEnabled(not self.completed)
        self.capture.setText("Capture and Register")
        self.name.setReadOnly(self.user is not None)
        if self.completed:
            self.progress.setValue(100)
            self.capture.hide()
            self.cancel.setText("Close")
            self.prompt.setText("Automatic recognition is ready. No re-registration is needed on future launches.")


class ManageUsersDialog(QDialog):
    def __init__(self, parent, token):
        super().__init__(parent)
        self.owner_window, self.store, self.token = parent, parent.database.users, token
        self.store.require_admin(token)
        self.setWindowTitle("Manage Users · Administrator")
        self.resize(510, 630)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(12)
        self.selector = QComboBox()
        self.selector.currentIndexChanged.connect(self.load_user)
        layout.addWidget(self.selector)
        form = QFormLayout()
        form.setVerticalSpacing(10)
        self.name = QLineEdit()
        self.name.setMaxLength(60)
        form.addRow("User name", self.name)
        self.role = QComboBox()
        self.role.addItem("Authorized User", "user")
        self.role.addItem("Administrator", "administrator")
        form.addRow("Role", self.role)
        self.active = QCheckBox("Account enabled")
        form.addRow(self.active)
        self.permission_widgets = {}
        for key, title in [("can_control_powerpoint", "PowerPoint control"), ("can_control_word", "Word control"),
                           ("can_use_gestures", "Gesture control"), ("can_manage_users", "User management (with administrative credential)"),
                           ("trusted_presence", "Trusted workstation presence")]:
            widget = QCheckBox(title)
            form.addRow(widget)
            self.permission_widgets[key] = widget
        self.role.currentIndexChanged.connect(lambda index: self.permission_widgets["can_manage_users"].setChecked(self.role.currentData() == "administrator"))
        layout.addLayout(form)
        layout.addWidget(label("Presentation access does not prevent owner-absence locking. Only the device owner and explicitly trusted users satisfy workstation presence."))
        self.owner_note = label()
        layout.addWidget(self.owner_note)
        self.message = label()
        layout.addWidget(self.message)
        row = QHBoxLayout()
        self.reenroll = QPushButton("Re-enroll face…")
        self.reenroll.clicked.connect(self.reenroll_user)
        row.addWidget(self.reenroll)
        self.delete_button = QPushButton("Delete…")
        self.delete_button.clicked.connect(self.delete_user)
        row.addWidget(self.delete_button)
        layout.addLayout(row)
        self.owner_button = QPushButton("Make device owner…")
        self.owner_button.clicked.connect(self.make_owner)
        layout.addWidget(self.owner_button)
        layout.addStretch()
        controls = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Close)
        controls.button(QDialogButtonBox.StandardButton.Save).setText("Save user")
        controls.accepted.connect(self.save_user)
        controls.rejected.connect(self.reject)
        layout.addWidget(controls)
        self.refresh()

    def refresh(self, selected=None):
        self.selector.blockSignals(True)
        self.selector.clear()
        for user in self.store.list_users():
            suffix = " · device owner" if user.is_owner else " · disabled" if not user.is_active else ""
            role = "Administrator" if user.role == "administrator" else "Authorized User"
            self.selector.addItem(f"{user.user_name} ({role}{suffix})", user.user_id)
        index = self.selector.findData(selected) if selected else 0
        self.selector.setCurrentIndex(max(0, index))
        self.selector.blockSignals(False)
        self.load_user()

    def current_user(self):
        user_id = self.selector.currentData()
        return self.store.get(user_id) if user_id else None

    def load_user(self, *args):
        user = self.current_user()
        if not user:
            self.message.setText("No registered users")
            return
        self.name.setText(user.user_name)
        self.role.setCurrentIndex(self.role.findData(user.role))
        self.active.setChecked(user.is_active)
        self.active.setEnabled(not user.is_owner)
        for key, widget in self.permission_widgets.items():
            widget.setChecked(getattr(user.permissions, key))
        self.permission_widgets["trusted_presence"].setEnabled(not user.is_owner)
        self.owner_button.setVisible(not user.is_owner)
        self.delete_button.setEnabled(not user.is_owner)
        self.owner_note.setText("Device owner · workstation presence trusted" if user.is_owner else "")
        self.selector.setToolTip(f"Registered: {user.created_at}\nLast recognized: {user.last_recognized_at or 'Not yet'}")

    def save_user(self):
        user = self.current_user()
        if not user:
            return
        try:
            permissions = Permissions(**{key: widget.isChecked() for key, widget in self.permission_widgets.items()})
            self.store.update(user.user_id, self.token, name=self.name.text(), role=self.role.currentData(),
                              active=self.active.isChecked(), permissions=permissions)
            self.owner_window.directory_changed()
            self.refresh(user.user_id)
            self.message.setText("User permissions saved.")
        except (ValueError, PermissionError, RuntimeError) as exc:
            self.message.setText(str(exc))

    def delete_user(self):
        user = self.current_user()
        if user and QMessageBox.question(self, "Delete user", f"Delete {user.user_name} and all their face templates?") == QMessageBox.StandardButton.Yes:
            try:
                self.store.delete(user.user_id, self.token)
                self.owner_window.directory_changed()
                self.refresh()
                self.message.setText("User and face templates deleted.")
            except (ValueError, PermissionError, RuntimeError) as exc:
                self.message.setText(str(exc))

    def make_owner(self):
        user = self.current_user()
        if user and QMessageBox.question(self, "Transfer device ownership",
            f"Make {user.user_name} the device owner? The previous owner remains trusted until you explicitly remove their presence permission.") == QMessageBox.StandardButton.Yes:
            try:
                self.store.set_owner(user.user_id, self.token)
                self.owner_window.directory_changed()
                self.refresh(user.user_id)
            except (ValueError, PermissionError, RuntimeError) as exc:
                self.message.setText(str(exc))

    def reenroll_user(self):
        user = self.current_user()
        if user:
            self.owner_window.open_registration(token=self.token, user=user)
            self.accept()
