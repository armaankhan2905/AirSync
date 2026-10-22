from types import SimpleNamespace as NS
from unittest.mock import MagicMock, Mock
import sys
from visionguard.startup import set_startup


def test_per_user_startup_registration_and_removal_are_explicit(monkeypatch):
    key = object()
    context = MagicMock()
    context.__enter__.return_value = key
    registry = NS(HKEY_CURRENT_USER=1, REG_SZ=1, CreateKey=Mock(return_value=context),
                  SetValueEx=Mock(), DeleteValue=Mock())
    monkeypatch.setitem(sys.modules, "winreg", registry)
    set_startup(True)
    args = registry.SetValueEx.call_args.args
    assert args[0] is key and args[1] == "VisionGuardAI"
    assert "pythonw.exe" in args[4] and "main.py" in args[4] and "--background" in args[4]
    set_startup(False)
    registry.DeleteValue.assert_called_once_with(key, "VisionGuardAI")


def test_startup_uses_frozen_executable_without_python_launcher(monkeypatch):
    context = MagicMock()
    registry = NS(HKEY_CURRENT_USER=1, REG_SZ=1, CreateKey=Mock(return_value=context),
                  SetValueEx=Mock(), DeleteValue=Mock())
    monkeypatch.setitem(sys.modules, "winreg", registry)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", r"C:\Program Files\VisionGuardAI\VisionGuardAI.exe")
    set_startup(True)
    command = registry.SetValueEx.call_args.args[4]
    assert command == '"C:\\Program Files\\VisionGuardAI\\VisionGuardAI.exe" --background'
