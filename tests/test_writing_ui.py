from types import SimpleNamespace as NS
from unittest.mock import Mock
import time
import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QDialog, QTabWidget
from visionguard.air_writing import AirWriting, SlideContext
from visionguard.config import Settings
from visionguard.writing_overlay import AnnotationOverlay
from visionguard.writing_controls import CalibrationDialog


@pytest.fixture
def window(qt_app, database):
    from visionguard.ui import MainWindow, STYLE
    qt_app.setStyleSheet(STYLE)
    view = MainWindow(database, Settings(), smoke=True)
    view.timer.stop()
    view.annotation_overlay.timer.stop()
    view.show()
    qt_app.processEvents()
    yield view
    if view.settings_dialog:
        view.settings_dialog.reject()
    view.close()
    qt_app.processEvents()


def test_settings_has_air_writing_page_and_preserves_camera_layout(window, qt_app):
    window.open_settings()
    tabs = window.settings_dialog.findChild(QTabWidget)
    assert [tabs.tabText(i) for i in range(tabs.count())] == ["General", "Air Writing"]
    assert window.centralWidget() == window.preview
    tabs.setCurrentIndex(1)
    qt_app.processEvents()
    for key in ("writing_enabled", "writing_color", "writing_width", "writing_smoothing",
                "writing_confidence", "writing_hold", "writing_indicator"):
        assert window.widgets[key].isVisible()


def test_writing_settings_are_validated_protected_and_persisted(window, database, monkeypatch):
    window.open_settings()
    window.widgets["writing_width"].setValue(9)
    window.widgets["writing_color"].setCurrentText("#ff00ff")
    monkeypatch.setattr(window, "authorize", lambda: False)
    assert not window.save_settings()
    assert database.settings().writing_width == 4
    monkeypatch.setattr(window, "authorize", lambda: True)
    window.writing_calibration_draft = [[.1,.1], [.9,.1], [.9,.9], [.1,.9]]
    assert window.save_settings()
    saved = database.settings()
    assert saved.writing_width == 9 and saved.writing_color == "#ff00ff"
    assert saved.writing_calibration == window.writing_calibration_draft


def test_cancel_discards_pen_and_calibration_changes(window):
    window.open_settings()
    window.widgets["writing_width"].setValue(12)
    window.writing_calibration_draft = [[.1,.1], [.9,.1], [.9,.9], [.1,.9]]
    window.settings_dialog.reject()
    window.open_settings()
    assert window.widgets["writing_width"].value() == 4
    assert window.writing_calibration_draft == Settings().writing_calibration


def test_calibration_reuses_preview_and_commits_only_four_good_corners(window, qt_app):
    samples = iter([None, (.2,.2), (.8,.2), (.8,.8), (.2,.8)])
    worker = NS(command=Mock(), calibration_sample=lambda: next(samples), writing_permit=lambda: True)
    window.worker = worker
    window.open_settings()
    window.calibrate_writing()
    dialog = window.calibration_dialog
    image = QImage(640, 480, QImage.Format.Format_RGB888)
    image.fill(Qt.GlobalColor.darkCyan)
    window.on_image(image)
    assert not dialog.preview._pixmap.isNull()
    dialog.capture_corner()
    assert not dialog.corners
    for _ in range(4):
        dialog.capture_corner()
    assert window.calibration_dialog is None
    assert window.writing_calibration_draft == [[.2,.2], [.8,.2], [.8,.8], [.2,.8]]
    assert worker.command.call_args_list[0].args == ("calibration_preview", True)
    assert worker.command.call_args_list[-1].args == ("calibration_preview", False)


def test_calibration_cancel_stops_pending_capture(qt_app):
    worker = NS(command=Mock(), calibration_sample=Mock())
    dialog = CalibrationDialog(worker)
    dialog.start_capture()
    assert dialog.timer.isActive()
    dialog.reject()
    assert not dialog.timer.isActive()
    worker.calibration_sample.assert_not_called()
    dialog.deleteLater()


def test_main_pen_indicator_respects_setting(window):
    window.on_status({"writing_mode": True})
    assert window.pen_indicator.isVisible()
    window.settings.writing_indicator = False
    window.on_status({"writing_mode": True})
    assert not window.pen_indicator.isVisible()


@pytest.fixture
def overlay(qt_app):
    context = SlideContext("show", 1, 123, (0,0,800,450), time.monotonic())
    engine = AirWriting(Settings())
    engine.context = context
    engine.mode = True
    engine.draw((.2,.3), time.monotonic())
    engine.draw((.3,.4), time.monotonic()+.05)
    worker = NS(air=engine, settings=Settings(), writing_housekeeping=lambda: None,
                writing_permit=lambda: True, office=NS(current_slide=lambda: context))
    view = AnnotationOverlay(lambda: worker)
    view.timer.stop()
    yield view, worker, context
    view.shutdown()
    view.deleteLater()


def test_overlay_is_transparent_clickthrough_nonactivating_and_focusless(overlay, qt_app):
    view, worker, context = overlay
    view.refresh()
    qt_app.processEvents()
    assert view.isVisible()
    assert view.windowFlags() & Qt.WindowType.WindowTransparentForInput
    assert view.windowFlags() & Qt.WindowType.WindowDoesNotAcceptFocus
    assert view.windowFlags() & Qt.WindowType.WindowStaysOnTopHint
    assert view.testAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
    assert view.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
    assert view.focusPolicy() == Qt.FocusPolicy.NoFocus
    assert not view.hasFocus()
    image = view.grab().toImage()
    assert image.pixelColor(700, 400).alpha() == 0
    assert image.pixelColor(160, 135).red() > 150


def test_overlay_resizes_without_changing_normalized_strokes(overlay, qt_app):
    view, worker, context = overlay
    view.refresh()
    points = worker.air.snapshot()["strokes"]
    view.resize(1600, 900)
    qt_app.processEvents()
    assert worker.air.snapshot()["strokes"] == points
    assert view.paths[0][2].elementAt(0).x == pytest.approx(.2)
    image = view.grab().toImage()
    assert image.pixelColor(320,270).red() > 150


def test_overlay_hides_on_face_loss_and_discards_cache_on_slideshow_exit(overlay):
    view, worker, context = overlay
    view.refresh()
    worker.writing_permit = lambda: False
    view.refresh()
    assert not view.isVisible() and not view.paths
    assert worker.air.ink
    worker.writing_permit = lambda: True
    worker.air.reconcile([], time.monotonic(), known=True)
    worker.office.current_slide = lambda: None
    view.refresh()
    assert not view.isVisible() and not view.paths and not worker.air.ink


def test_native_placement_never_requests_keyboard_activation(overlay, monkeypatch):
    import win32gui
    import win32con
    view, _, _ = overlay
    setter = Mock()
    styles = Mock()
    monkeypatch.setattr(win32gui, "GetWindowLong", lambda *args: 0)
    monkeypatch.setattr(win32gui, "SetWindowLong", styles)
    monkeypatch.setattr(win32gui, "SetWindowPos", setter)
    monkeypatch.setattr(view, "show", lambda: None)
    view.place_native((-1920, 60, 1920, 1080))
    args = setter.call_args.args
    assert args[1] == win32con.HWND_TOPMOST and args[2:6] == (-1920,60,1920,1080)
    assert args[-1] & win32con.SWP_NOACTIVATE
    assert styles.call_args.args[-1] & win32con.WS_EX_NOACTIVATE
    assert styles.call_args.args[-1] & win32con.WS_EX_TRANSPARENT


def test_path_cache_reuses_existing_points_and_resets_after_clear(overlay):
    view, worker, context = overlay
    frame = worker.air.snapshot()
    view.set_frame(frame)
    original_path = view.paths[0][2]
    worker.air.draw((.4,.5), time.monotonic()+.1)
    view.set_frame(worker.air.snapshot())
    assert view.paths[0][2] is original_path
    assert view.paths[0][4] == 3
    worker.air.clear(context.key)
    view.set_frame(worker.air.snapshot())
    assert not view.paths
