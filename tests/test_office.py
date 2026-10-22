from types import SimpleNamespace as NS
from unittest.mock import Mock
import pytest
from visionguard.office import OfficeWorker, PowerPointController, Target, WordController
from visionguard.config import default_mappings


@pytest.fixture
def powerpoint():
    view = NS(Slide=NS(SlideIndex=2), Next=Mock(), Previous=Mock(), Exit=Mock())
    settings = NS(StartingSlide=1, EndingSlide=3, RangeType=2)
    slides = NS(Count=3, Item=lambda i: NS(SlideShowTransition=NS(Hidden=0)))
    window = NS(HWND=100, View=view, Presentation=NS(SlideShowSettings=settings, Slides=slides))
    collection = NS(Count=1, Item=lambda i: window)
    app = NS(SlideShowWindows=collection, Quit=Mock())
    return app, window


@pytest.mark.parametrize("action,method", [("next", "Next"), ("previous", "Previous"), ("exit", "Exit")])
def test_powerpoint_actions_do_not_close_document(powerpoint, action, method):
    app, win = powerpoint
    ctrl = PowerPointController(app, lambda a, b: a == b)
    ctrl.execute(action, 100)
    getattr(win.View, method).assert_called_once()
    app.Quit.assert_not_called()


def test_powerpoint_slide_boundaries(powerpoint):
    app, win = powerpoint
    ctrl = PowerPointController(app, lambda a, b: a == b)
    win.View.Slide.SlideIndex = 3
    assert ctrl.execute("next", 100) == "Already at last slide"
    win.View.Next.assert_not_called()
    win.View.Slide.SlideIndex = 1
    assert ctrl.execute("previous", 100) == "Already at first slide"
    win.View.Previous.assert_not_called()


def test_powerpoint_hidden_trailing_slide_and_custom_show(powerpoint):
    app, win = powerpoint
    win.Presentation.Slides.Item = lambda i: NS(SlideShowTransition=NS(Hidden=-1 if i == 3 else 0))
    ctrl = PowerPointController(app, lambda a, b: a == b)
    ctrl.execute("next", 100)
    win.View.Next.assert_not_called()
    win.Presentation.SlideShowSettings.RangeType = 3
    assert "Custom" in ctrl.execute("next", 100)
    win.View.Next.assert_not_called()


def test_powerpoint_guard_revoked_after_COM_access(powerpoint):
    app, win = powerpoint
    ctrl = PowerPointController(app, lambda a, b: a == b, guard=lambda: False)
    assert "cancelled" in ctrl.execute("exit", 100)
    win.View.Exit.assert_not_called()


def test_word_guard_revoked_after_COM_access(word):
    ctrl = WordController(word, lambda a, b: a == b, guard=lambda: False)
    assert "changed" in ctrl.execute("next", 200)
    word.ActiveWindow.ActivePane.PageScroll.assert_not_called()


def test_no_slideshow_wrong_window_no_commands(powerpoint):
    app, win = powerpoint
    ctrl = PowerPointController(app, lambda a, b: a == b)
    assert "No foreground" in ctrl.execute("next", 999)
    app.SlideShowWindows.Count = 0
    assert "No foreground" in ctrl.execute("exit", 100)
    win.View.Next.assert_not_called()
    win.View.Exit.assert_not_called()


@pytest.fixture
def word():
    pane = NS(PageScroll=Mock())
    window = NS(Hwnd=200, View=NS(Type=3), ActivePane=pane, LargeScroll=Mock())
    return NS(Documents=NS(Count=1), ActiveWindow=window, Selection=Mock())


@pytest.mark.parametrize("action,kwargs", [("next", {"Down": 1}), ("previous", {"Up": 1})])
def test_word_page_navigation_preserves_selection(word, action, kwargs):
    WordController(word, lambda a, b: a == b).execute(action, 200)
    word.ActiveWindow.ActivePane.PageScroll.assert_called_once_with(**kwargs)
    assert word.Selection.mock_calls == []


def test_word_views_focus_and_no_document(word):
    ctrl = WordController(word, lambda a, b: a == b)
    assert "focus changed" in ctrl.execute("next", 999)
    word.ActiveWindow.View.Type = 7
    assert "one screen" in ctrl.execute("next", 200)
    word.ActiveWindow.LargeScroll.assert_called_once_with(Down=1)
    word.ActiveWindow.View.Type = 1
    assert "Print Layout" in ctrl.execute("next", 200)
    word.Documents.Count = 0
    assert "No Word document" in ctrl.execute("next", 200)
    word.ActiveWindow.ActivePane.PageScroll.assert_not_called()


def test_mapping_defaults():
    maps = default_mappings()
    assert maps["PowerPoint"] == {"LEFT_L": "previous", "RIGHT_L": "next", "FIST": "exit", "PINCH": "exit"}
    assert maps["Word"]["FIST"] == "pause"
    assert maps["Word"]["PINCH"] == "toggle"


def test_office_commands_expire_and_focus_change():
    worker = OfficeWorker(permit=lambda: True, get_foreground=lambda: (100, "screenClass"))
    worker.target = Target("PowerPoint", 100, "ready")
    getter = Mock()
    assert "stale" in worker.dispatch((worker.target, "next", 0), getter, 2)
    assert "changed" in worker.dispatch((Target("Word", 200, "ready"), "next", 2), getter, 2.1)
    getter.assert_not_called()


def test_unverified_office_never_dispatches():
    worker = OfficeWorker(permit=lambda: False)
    getter = Mock()
    assert "unverified" in worker.dispatch((Target("PowerPoint", 100), "next", 1), getter, 1.1)
    getter.assert_not_called()


def test_word_pause_and_pinch_resume():
    w = OfficeWorker(permit=lambda: True, get_foreground=lambda: (200, "OpusApp"))
    w.target = Target("Word", 200)
    getter = Mock()
    assert "paused" in w.dispatch((w.target, "pause", 1), getter, 1.1)
    assert "paused" in w.dispatch((w.target, "next", 1), getter, 1.2)
    getter.assert_not_called()
    assert "resumed" in w.dispatch((w.target, "toggle", 1), getter, 1.3)


def test_unsupported_foreground_does_not_connect_COM():
    w = OfficeWorker(get_foreground=lambda: (500, "Notepad"))
    getter = Mock()
    assert w.detect(getter).app == "None"
    getter.assert_not_called()


def test_success_feedback_is_emitted_only_after_controller_execution(powerpoint, monkeypatch):
    import time
    import visionguard.office as office
    from visionguard.office import GestureEvent
    app, win = powerpoint
    monkeypatch.setattr(office, "same_window", lambda a, b: a == b)
    feedback = []
    worker = OfficeWorker(permit=lambda: True, get_foreground=lambda: (100, "screenClass"), on_result=feedback.append)
    worker.target = Target("PowerPoint", 100, "Slideshow ready")
    event = GestureEvent("RIGHT_L", "Right", .99, time.monotonic(), True, True, 0, worker.target, "next")
    assert worker.submit(event)
    assert feedback == []  # Queueing cannot generate a successful notification.
    win.View.Next.side_effect = lambda: feedback.append("executed")
    worker.dispatch(worker.commands.get_nowait(), lambda _: app, time.monotonic())
    assert feedback[0] == "executed"
    assert feedback[1]["success"] is True
    assert feedback[1]["gesture"] == "RIGHT_L"
    win.View.Slide.SlideIndex = 3
    event = GestureEvent("RIGHT_L", "Right", .99, time.monotonic(), True, True, 0, worker.target, "next")
    worker.dispatch(event, lambda _: app, time.monotonic())
    assert feedback[-1]["success"] is False
    assert feedback[-1]["result"] == "Already at last slide"


def test_rejection_feedback_identifies_the_original_gesture():
    from visionguard.office import GestureEvent
    feedback = []
    worker = OfficeWorker(on_result=feedback.append)
    event = GestureEvent("FIST", "Left", .99, 1., True, False, 0, Target("PowerPoint", 100), "exit")
    worker.dispatch(event, Mock(), 1.1)
    assert feedback[-1]["success"] is False
    assert feedback[-1]["gesture"] == "FIST"


def test_guard_revoked_at_actual_navigation_is_not_reported_as_success(powerpoint):
    app, win = powerpoint
    guards = iter([True, False])
    controller = PowerPointController(app, lambda a, b: a == b, lambda: next(guards))
    assert "cancelled" in controller.execute("next", 100)
    win.View.Next.assert_not_called()
