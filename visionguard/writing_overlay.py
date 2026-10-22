"""GUI-thread, non-activating transparent ink surface. Never accesses Office COM."""
import sys
import logging
from PySide6.QtCore import Qt, QTimer, QPointF
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget, QApplication
from .air_writing import fit_slide
from .windows_office import slideshow_client_rect


class AnnotationOverlay(QWidget):
    def __init__(self, worker_provider, parent=None):
        # Deliberately no native parent: drawing belongs over PowerPoint, not the camera.
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool |
                         Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.WindowTransparentForInput |
                         Qt.WindowType.WindowDoesNotAcceptFocus)
        self.worker_provider = worker_provider
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setStyleSheet("background: transparent;")
        self.frame = {}
        self.paths = []
        self._cache_key = None
        self._path_generation = None
        self._native_rect = None
        self._last_error = None
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(33)

    def conceal(self):
        self.hide()
        self.frame = {}
        self.paths = []
        self._cache_key = None
        self._path_generation = None

    def refresh(self):
        worker = self.worker_provider()
        if worker is None or not hasattr(worker, "air"):
            self.conceal()
            return
        try:
            worker.writing_housekeeping()
            context = worker.office.current_slide()
            if not worker.settings.writing_enabled or not worker.writing_permit() or context is None:
                self.conceal()
                return
            frame = worker.air.snapshot()
            if not frame["context"] or frame["context"].key != context.key:
                self.conceal()
                return
            frame["indicator"] = worker.settings.writing_indicator
            self.set_frame(frame)
            if not frame["strokes"] and not frame["indicator"]:
                self.conceal()
                return
            if sys.platform == "win32" and QApplication.platformName() != "offscreen":
                rect = fit_slide(slideshow_client_rect(context.hwnd), context.rect[2] / context.rect[3])
                self.place_native(rect)
            else:
                self.setGeometry(*context.rect)
                self.show()
            self._last_error = None
        except Exception as exc:
            # A destroyed/moving window or stale worker must not leave stray ink.
            self.conceal()
            if type(exc) != self._last_error:
                logging.getLogger(__name__).exception("Annotation overlay unavailable")
            self._last_error = type(exc)

    def set_frame(self, frame):
        self.frame = frame
        key = (frame["context"].key, frame["revision"])
        if key != self._cache_key:
            generation = (frame["context"].key, frame.get("generation", 0))
            previous = self.paths if generation == self._path_generation else []
            paths = []
            for i, (color, width, points) in enumerate(frame["strokes"]):
                if not points:
                    continue
                old = previous[i] if i < len(previous) else None
                reusable = old and old[:2] == (color, width) and old[3] == points[0] and old[4] <= len(points)
                path = old[2] if reusable else QPainterPath(QPointF(*points[0]))
                start = old[4] if reusable else 1
                for point in points[start:]:
                    path.lineTo(QPointF(*point))
                paths.append((color, width, path, points[0], len(points)))
            self.paths = paths
            self._path_generation = generation
            self._cache_key = key
        self.update()

    def place_native(self, rect):
        import win32con
        import win32gui
        hwnd = int(self.winId())
        style = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
        needed = win32con.WS_EX_LAYERED | win32con.WS_EX_TRANSPARENT | win32con.WS_EX_NOACTIVATE | win32con.WS_EX_TOOLWINDOW
        if style & needed != needed:
            win32gui.SetWindowLong(hwnd, win32con.GWL_EXSTYLE, style | needed)
        self.show()  # WA_ShowWithoutActivating and WindowDoesNotAcceptFocus.
        # Win32 uses physical coordinates even on mixed-DPI/negative-origin monitors.
        win32gui.SetWindowPos(hwnd, win32con.HWND_TOPMOST, *rect,
                              win32con.SWP_NOACTIVATE | win32con.SWP_NOOWNERZORDER)
        self._native_rect = rect

    def nativeEvent(self, event_type, message):
        if sys.platform == "win32":
            import ctypes
            from ctypes import wintypes
            msg = wintypes.MSG.from_address(int(message))
            if msg.message == 0x0084:  # WM_NCHITTEST: pass clicks through.
                return True, -1
            if msg.message == 0x0021:  # WM_MOUSEACTIVATE: never activate.
                return True, 3
        return super().nativeEvent(event_type, message)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.save()
        painter.scale(self.width(), self.height())
        for color, width, path, first, count in self.paths:
            pen = QPen(QColor(color), width, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
            pen.setCosmetic(True)
            painter.setPen(pen)
            if count == 1:
                painter.drawPoint(QPointF(*first))
            else:
                painter.drawPath(path)
        painter.restore()
        if self.frame.get("indicator"):
            painter.setPen(QColor("#ffffff"))
            painter.setBrush(QColor(12, 20, 34, 190))
            painter.drawRoundedRect(12, 12, 180, 32, 8, 8)
            painter.setPen(QPen(QColor("#68d9ef"), 2))
            painter.drawLine(23, 34, 33, 24)
            painter.drawLine(23, 34, 23, 30)
            painter.setPen(QColor("#ffffff"))
            painter.drawText(42, 34, "Writing Mode ON" if self.frame.get("mode") else "Writing Mode OFF")
        painter.end()

    def shutdown(self):
        self.timer.stop()
        self.conceal()
        self.close()
