"""Air Writing settings and a four-corner calibration using the existing camera."""
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QLabel, QPushButton, QVBoxLayout
from .air_writing import CoordinateMapper
from .camera_view import CameraView


class CalibrationDialog(QDialog):
    CORNERS = ("top left", "top right", "bottom right", "bottom left")

    def __init__(self, worker, parent=None):
        super().__init__(parent)
        self.worker = worker
        self.corners = []
        self.setWindowTitle("Air Writing · Comfortable reach")
        self.resize(620, 600)
        layout = QVBoxLayout(self)
        self.preview = CameraView()
        self.preview.setMinimumSize(440, 330)
        layout.addWidget(self.preview, 1)
        self.prompt = QLabel("Use only your index finger. Keep your face visible.\n"
                             "Capture the four comfortable reach corners in the mirrored preview.\n"
                             "No drawing or slide navigation happens during calibration.")
        self.prompt.setWordWrap(True)
        layout.addWidget(self.prompt)
        self.capture = QPushButton("Capture top left in 3 seconds")
        self.capture.clicked.connect(self.start_capture)
        layout.addWidget(self.capture)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.capture_corner)
        worker.command("calibration_preview", True)
        self.finished.connect(self.finish)

    def start_capture(self):
        self.capture.setEnabled(False)
        self.prompt.setText(f"Point at your comfortable {self.CORNERS[len(self.corners)]} reach. Hold only your index finger up…")
        self.timer.start(3000)

    def capture_corner(self):
        self.capture.setEnabled(True)
        sample = self.worker.calibration_sample()
        if sample is None:
            self.prompt.setText("Keep one verified face and one index-finger hand visible, then retry this corner.")
            return
        self.corners.append(list(sample))
        if len(self.corners) == 4:
            try:
                CoordinateMapper(self.corners)
            except ValueError as exc:
                self.corners = []
                self.prompt.setText(str(exc))
            else:
                self.accept()
                return
        self.capture.setText(f"Capture {self.CORNERS[len(self.corners)]} in 3 seconds")

    def finish(self):
        self.timer.stop()
        self.worker.command("calibration_preview", False)
