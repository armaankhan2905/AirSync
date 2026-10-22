import os
from unittest.mock import Mock
import pytest
import win32gui
import win32process
from visionguard.windows_office import window_info


@pytest.mark.parametrize("own_window", [False, True])
def test_window_detection_uses_PID_and_executable_without_retaining_title(monkeypatch, own_window):
    pid = os.getpid() if own_window else os.getpid() + 100
    title = Mock(return_value="private-document-name")
    monkeypatch.setattr(win32gui, "IsWindow", lambda hwnd: True)
    monkeypatch.setattr(win32gui, "GetClassName", lambda hwnd: "PPTFrameClass")
    monkeypatch.setattr(win32gui, "GetWindowText", title)
    monkeypatch.setattr(win32process, "GetWindowThreadProcessId", lambda hwnd: (1, pid))
    info = window_info(100, {pid: "POWERPNT.EXE"})
    assert info.pid == pid and info.exe == "POWERPNT.EXE"
    assert "private-document-name" not in repr(info)
    if own_window:
        title.assert_not_called()  # no Qt main-thread WM_GETTEXT shutdown dependency
    else:
        title.assert_called_once_with(100)
