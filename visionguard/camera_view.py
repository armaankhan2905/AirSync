"""Camera canvas and transient feedback; never owns capture or inference."""
from PySide6.QtCore import Qt, QRectF, QTimer, QPropertyAnimation, QEasingCurve
from PySide6.QtGui import QColor, QPainter, QPixmap
from PySide6.QtWidgets import QWidget, QSizePolicy, QFrame, QLabel, QVBoxLayout, QGraphicsOpacityEffect


class CameraView(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("CameraView")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._pixmap = QPixmap()
        self.message = "Camera is off\nComplete local setup to begin"

    def set_image(self, image):
        # Cache one native-size pixmap. Paint-time scaling also handles resizing
        # when capture is paused, with no extra frame copies or scaled caches.
        self._pixmap = QPixmap.fromImage(image)
        self.message = ""
        self.update()

    def clear(self):
        self._pixmap = QPixmap()
        self.update()

    def setText(self, message):
        self.clear()
        self.message = message
        self.update()

    def image_rect(self):
        if self._pixmap.isNull():
            return QRectF()
        size = self._pixmap.size().scaled(self.size(), Qt.AspectRatioMode.KeepAspectRatio)
        return QRectF((self.width() - size.width()) / 2, (self.height() - size.height()) / 2,
                      size.width(), size.height())

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#050a10"))
        if not self._pixmap.isNull():
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            painter.drawPixmap(self.image_rect(), self._pixmap, QRectF(self._pixmap.rect()))
        else:
            painter.setPen(QColor("#91a5b7"))
            area = self.rect()
            guide = getattr(self.window(), "guide", None)
            if guide is not None and guide.isVisible():
                area.setTop(min(self.height() - 100, guide.geometry().bottom() + 20))
                area.setBottom(self.height() - 80)
            painter.drawText(area, Qt.AlignmentFlag.AlignCenter, self.message)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.parent() and hasattr(self.parent(), "position_overlays"):
            self.parent().position_overlays()


class ActionToast(QFrame):
    """A non-focusable in-window notification; never raises the app."""
    def __init__(self, parent):
        super().__init__(parent)
        self.setObjectName("ActionToast")
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setMaximumWidth(420)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 16, 24, 16)
        layout.setSpacing(6)
        self.caption = QLabel()
        self.caption.setObjectName("ToastCaption")
        self.title = QLabel()
        self.title.setObjectName("ToastTitle")
        for label in (self.caption, self.title):
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            label.setWordWrap(True)
            label.setTextFormat(Qt.TextFormat.PlainText)
            layout.addWidget(label)
        self.opacity = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self.opacity)
        self.animation = QPropertyAnimation(self.opacity, b"opacity", self)
        self.animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.animation.finished.connect(self._animation_finished)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.dismiss)
        self._fading_out = False
        self.hide()

    def present(self, caption, title, success=False):
        self.animation.stop()
        self.timer.stop()
        self._fading_out = False
        self.caption.setText(caption)
        self.title.setText(title)
        color = "#75edb8" if success else "#ffd27b"
        self.title.setStyleSheet(f"color:{color}; background:transparent;")
        self.setFixedWidth(min(420, max(260, self.parentWidget().width() - 48)))
        self.adjustSize()
        self.show()
        self.raise_()
        self.parentWidget().window().position_overlays()
        self.opacity.setOpacity(0)
        self.animation.setDuration(160)
        self.animation.setStartValue(0.)
        self.animation.setEndValue(1.)
        self.animation.start()
        self.timer.start(1600)

    def dismiss(self):
        self.animation.stop()
        self._fading_out = True
        self.animation.setDuration(220)
        self.animation.setStartValue(self.opacity.opacity())
        self.animation.setEndValue(0.)
        self.animation.start()

    def _animation_finished(self):
        if self._fading_out:
            self.hide()
