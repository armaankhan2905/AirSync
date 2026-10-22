"""Explicit, per-user Windows Run entry; never installed by tests or imports."""
from pathlib import Path
import subprocess
import sys


def set_startup(enabled: bool):
    import winreg
    key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key_path) as key:
        if enabled:
            if getattr(sys, "frozen", False):
                args = [sys.executable, "--background"]
            else:
                pythonw = Path(sys.executable).with_name("pythonw.exe")
                args = [str(pythonw), str(Path(__file__).resolve().parent.parent / "main.py"), "--background"]
            winreg.SetValueEx(key, "VisionGuardAI", 0, winreg.REG_SZ, subprocess.list2cmdline(args))
        else:
            try:
                winreg.DeleteValue(key, "VisionGuardAI")
            except FileNotFoundError:
                pass
