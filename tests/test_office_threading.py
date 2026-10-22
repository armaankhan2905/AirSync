import sys
import threading
import time
from types import SimpleNamespace as NS
from unittest.mock import Mock
import win32com.client
import visionguard.office as office
from visionguard.office import OfficeWorker, GestureEvent, Target
from visionguard.windows_office import WindowInfo
from visionguard.config import Settings
from visionguard.security import Observation
from visionguard.state import StateManager


def test_COM_init_execution_and_cleanup_stay_on_one_worker_thread(monkeypatch):
    calls = []
    done = threading.Event()
    def record(name):
        calls.append((name, threading.get_ident()))
    com = NS(COINIT_APARTMENTTHREADED=2, CoInitializeEx=lambda mode: record("init"),
             CoUninitialize=lambda: record("cleanup"), PumpWaitingMessages=lambda: None)
    monkeypatch.setitem(sys.modules, "pythoncom", com)
    monkeypatch.setattr(office, "same_window", lambda a, b: a == b)
    inv = {"PowerPoint": False, "Word": True, "processes": {1: "WINWORD.EXE"}}
    w = OfficeWorker(permit=lambda: True, get_foreground=lambda: (100, "OpusApp"),
                     get_inventory=lambda: inv, get_window=lambda *a: WindowInfo(100, 1, "WINWORD.EXE", "OpusApp"))
    def scroll(**kwargs):
        record("scroll")
        w.stop_event.set()
        done.set()
    app = NS(Documents=NS(Count=1), ActiveWindow=NS(Hwnd=100, View=NS(Type=3),
                                                ActivePane=NS(PageScroll=scroll)))
    def getter(name):
        record("get_object")
        return app
    monkeypatch.setattr(win32com.client, "GetActiveObject", getter)
    target = Target("Word", 100, "Word ready", "document", 100)
    w.commands.put_nowait(GestureEvent("RIGHT_L", "Right", .99, time.monotonic(), True, True, 0, target, "next"))
    w.start()
    try:
        assert done.wait(2)
    finally:
        w.stop()
    assert {name for name, _ in calls} >= {"init", "get_object", "scroll", "cleanup"}
    assert len({thread for _, thread in calls}) == 1
    assert calls[0][1] != threading.get_ident()


def test_queued_gesture_cancelled_after_auth_loss_even_after_reverification(monkeypatch):
    state = StateManager(Settings())
    for t in (10., 10.2, 10.4):
        state.update_auth(Observation(True), t, face_count=1)
    target = Target("PowerPoint", 100, "Slideshow ready")
    event = GestureEvent("RIGHT_L", "Right", .99, 10.4, True, True, state.snapshot().auth_epoch, target, "next")
    state.update_auth(Observation(unknown_count=1), 10.5, face_count=1)
    for t in (10.6, 10.7, 10.8):
        state.update_auth(Observation(True), t, face_count=1)
    w = OfficeWorker(permit=lambda: True, state=state)
    w.target = target
    getter = Mock()
    assert "authentication changed" in w.dispatch(event, getter, 10.8)
    getter.assert_not_called()


def test_queue_overflow_is_reported_instead_of_silently_dropped():
    w = OfficeWorker(permit=lambda: True)
    target = Target("PowerPoint", 100, "Slideshow ready")
    event = GestureEvent("RIGHT_L", "Right", .99, 10, True, True, 0, target, "next")
    assert w.submit(event)
    assert not w.submit(event)
    assert "queue full" in w.status
