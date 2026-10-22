from types import SimpleNamespace as NS
from unittest.mock import Mock
import pytest
import visionguard.office as office
from visionguard.office import OfficeWorker, Target, GestureEvent
from visionguard.windows_office import WindowInfo


def worker(monkeypatch, *, exe="VisionGuardAI.exe", ppt=True, word=False):
    monkeypatch.setattr(office, "same_window", lambda a, b: a == b)
    inv = {"PowerPoint": ppt, "Word": word, "processes": {10: exe},
           "installed": {"PowerPoint": True, "Word": True}}
    return OfficeWorker(permit=lambda: True, get_foreground=lambda: (100, "anyClass"),
                        get_inventory=lambda: inv,
                        get_window=lambda hwnd, processes: WindowInfo(hwnd, 10, exe, "anyClass"))


def ppt(count=0, hwnd=100):
    view = NS(Slide=NS(SlideIndex=2), Next=Mock(), Previous=Mock(), Exit=Mock())
    slides = NS(Count=3, Item=lambda i: NS(SlideShowTransition=NS(Hidden=0)))
    win = NS(HWND=hwnd, View=view, Presentation=NS(Slides=slides, SlideShowSettings=NS(RangeType=1)))
    return NS(SlideShowWindows=NS(Count=count, Item=lambda i: win)), win


def test_background_process_detected_without_foreground_title_or_class(monkeypatch):
    w = worker(monkeypatch)
    app, _ = ppt()
    target = w.detect(lambda progid: app)
    assert target.app == "PowerPoint"
    assert target.status == "PowerPoint Running — No Active Slideshow"
    assert not target.ready
    assert w.details["PowerPoint_running"] and w.details["PowerPoint_COM"] == "connected"


def test_running_process_remains_visible_when_COM_unavailable(monkeypatch):
    w = worker(monkeypatch)
    target = w.detect(Mock(side_effect=RuntimeError("COM busy")))
    assert target.app == "PowerPoint" and "COM unavailable" in target.status
    assert not target.ready


def test_ppt_process_detects_slideshow_with_unfamiliar_window_class(monkeypatch):
    w = worker(monkeypatch, exe="POWERPNT.EXE")
    app, _ = ppt(1)
    target = w.detect(lambda _: app)
    assert target.ready and target.hwnd == 100
    assert w.details["active_slideshow"]


def test_other_monitor_slideshow_with_foreground_presenter_window(monkeypatch):
    w = worker(monkeypatch, exe="POWERPNT.EXE")
    app, _ = ppt(1, hwnd=200)
    target = w.detect(lambda _: app)
    assert target.ready and target.hwnd == 200 and target.focus_hwnd == 100


def test_background_slideshow_never_controls_unrelated_foreground(monkeypatch):
    w = worker(monkeypatch, exe="NOTEPAD.EXE")
    app, _ = ppt(1, hwnd=200)
    target = w.detect(lambda _: app)
    assert target.app == "PowerPoint" and not target.ready
    assert "Focus PowerPoint" in target.status


def test_word_detection_by_process_and_document_HWND(monkeypatch):
    w = worker(monkeypatch, exe="WINWORD.EXE", ppt=False, word=True)
    target = w.detect(lambda _: NS(Documents=NS(Count=1), ActiveWindow=NS(Hwnd=100)))
    assert target.app == "Word" and target.ready
    assert w.details["Word_COM"] == "connected"


def test_fake_powerpoint_window_class_does_not_authorize_wrong_executable(monkeypatch):
    w = worker(monkeypatch, exe="NOTEPAD.EXE", ppt=False)
    getter = Mock()
    assert w.detect(getter).app == "None"
    getter.assert_not_called()


@pytest.mark.parametrize("name,hand,command,method", [
    ("RIGHT_L", "Right", "next", "Next"), ("LEFT_L", "Left", "previous", "Previous"),
    ("FIST", "Left", "exit", "Exit"), ("PINCH", "Right", "exit", "Exit")])
def test_gesture_event_reaches_powerpoint_COM(monkeypatch, name, hand, command, method):
    w = worker(monkeypatch, exe="POWERPNT.EXE")
    app, win = ppt(1)
    w.target = w.detect(lambda _: app)
    event = GestureEvent(name, hand, .95, 10, True, True, 0, w.target, command)
    monkeypatch.setattr(office.time, "monotonic", lambda: 10.1)
    assert "PowerPoint" in w.dispatch(event, lambda _: app, 10.1) or command == "exit"
    getattr(win.View, method).assert_called_once()


@pytest.mark.parametrize("stable,authorized,issued,command,reason", [
    (False, True, 10, "next", "not stabilized"), (True, False, 10, "next", "unverified"),
    (True, True, 8, "next", "stale"), (True, True, 10, "delete", "invalid")])
def test_precise_dispatch_rejections(monkeypatch, stable, authorized, issued, command, reason):
    w = worker(monkeypatch, exe="POWERPNT.EXE")
    w.target = Target("PowerPoint", 100, "Slideshow ready")
    event = GestureEvent("RIGHT_L", "Right", .95, issued, stable, authorized, 0, w.target, command)
    getter = Mock()
    assert reason in w.dispatch(event, getter, 10.1)
    getter.assert_not_called()
