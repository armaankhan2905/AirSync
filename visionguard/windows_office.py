"""Read-only Windows Office inventory; never stores document/window titles."""
from dataclasses import dataclass
from functools import lru_cache
import ctypes
from ctypes import wintypes
import os


@dataclass(frozen=True)
class WindowInfo:
    hwnd: int = 0
    pid: int = 0
    exe: str = ""
    class_name: str = ""


def process_names():
    class Entry(ctypes.Structure):
        _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
                    ("th32ProcessID", wintypes.DWORD), ("th32DefaultHeapID", ctypes.c_size_t),
                    ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
                    ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", wintypes.LONG),
                    ("dwFlags", wintypes.DWORD), ("szExeFile", wintypes.WCHAR * 260)]
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(Entry)]
    kernel.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(Entry)]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.CreateToolhelp32Snapshot(2, 0)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    names = {}
    try:
        entry = Entry()
        entry.dwSize = ctypes.sizeof(entry)
        ok = kernel.Process32FirstW(handle, ctypes.byref(entry))
        while ok:
            names[int(entry.th32ProcessID)] = entry.szExeFile.upper()
            ok = kernel.Process32NextW(handle, ctypes.byref(entry))
    finally:
        kernel.CloseHandle(handle)
    return names


def window_info(hwnd=None, processes=None):
    import win32gui
    import win32process
    hwnd = win32gui.GetForegroundWindow() if hwnd is None else hwnd
    if not hwnd or not win32gui.IsWindow(hwnd):
        return WindowInfo()
    _, pid = win32process.GetWindowThreadProcessId(hwnd)
    cls = win32gui.GetClassName(hwnd)
    # Readable HWND metadata is useful for detection, but titles may contain
    # private document names and must never enter logs or diagnostics.
    # GetWindowText on our own Qt window may send WM_GETTEXT to the GUI
    # thread, which can be waiting for worker shutdown. Avoid that cross-thread
    # dependency. Remote titles are read only as metadata and immediately dropped.
    if pid != os.getpid():
        win32gui.GetWindowText(hwnd)
    exe = (processes if processes is not None else process_names()).get(pid, "")
    return WindowInfo(hwnd, pid, exe, cls)


@lru_cache(maxsize=1)
def installed_office():
    import winreg
    found = {"PowerPoint": False, "Word": False}
    for app, exe in (("PowerPoint", "POWERPNT.EXE"), ("Word", "WINWORD.EXE")):
        for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
                try:
                    with winreg.OpenKey(hive, rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{exe}",
                                        0, winreg.KEY_READ | view) as key:
                        path = winreg.QueryValue(key, None).strip('"')
                        found[app] |= os.path.isfile(path)
                except OSError:
                    pass
    return found


def inventory():
    names = process_names()
    return {"processes": names, "installed": installed_office(),
            "PowerPoint": "POWERPNT.EXE" in names.values(),
            "Word": "WINWORD.EXE" in names.values()}
def slideshow_client_rect(hwnd):
    """Physical pixels in the caller's per-monitor DPI context, no window titles."""
    import win32gui
    if not win32gui.IsWindow(hwnd) or not win32gui.IsWindowVisible(hwnd) or win32gui.IsIconic(hwnd):
        raise ValueError("Slideshow window unavailable")
    left, top, right, bottom = win32gui.GetClientRect(hwnd)
    x, y = win32gui.ClientToScreen(hwnd, (left, top))
    return x, y, right - left, bottom - top
