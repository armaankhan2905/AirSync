"""Foreground-scoped Office COM control. No global key injection or app launch."""
from dataclasses import dataclass
import ctypes
import logging
import queue
import threading
import time
import uuid
import hashlib
from .windows_office import inventory, window_info, slideshow_client_rect
from .air_writing import SlideContext, fit_slide

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Target:
    app: str = "None"
    hwnd: int = 0
    status: str = "Focus a supported Office window"
    kind: str = ""
    focus_hwnd: int = 0

    @property
    def ready(self) -> bool:
        return (self.kind in {"slideshow", "document"} or
                (not self.kind and self.status in {"Slideshow ready", "Word ready"}))

    @property
    def key(self):
        return self.app, self.hwnd, self.focus_hwnd or self.hwnd, self.kind


@dataclass(frozen=True)
class GestureEvent:
    gesture: str
    hand: str
    confidence: float
    timestamp: float
    stable: bool
    authorized: bool
    auth_epoch: int
    target: Target
    command: str
    user_id: str | None = None


def foreground() -> tuple[int, str]:
    import win32gui
    hwnd = win32gui.GetForegroundWindow()
    return hwnd, win32gui.GetClassName(hwnd) if hwnd else ""


def same_window(a: int, b: int) -> bool:
    import win32gui
    return bool(a and b and win32gui.GetAncestor(a, 2) == win32gui.GetAncestor(b, 2))


class PowerPointController:
    def __init__(self, app, window_match=None, guard=lambda: True):
        self.app, self.window_match = app, window_match or same_window
        self.guard = guard

    def window(self, hwnd):
        windows = self.app.SlideShowWindows
        for i in range(1, windows.Count + 1):
            win = windows.Item(i)
            if self.window_match(int(win.HWND), hwnd):
                return win
        return None

    def execute(self, action: str, hwnd: int) -> str:
        window = self.window(hwnd)
        if window is None:
            return "No foreground PowerPoint slideshow"
        view = window.View
        if not self.guard():
            return "Focus or verification changed; action cancelled"
        if action == "exit":
            view.Exit()
            return "Exited slideshow; presentation remains open"
        settings = window.Presentation.SlideShowSettings
        if int(settings.RangeType) == 3:
            return "Custom slide shows require a standard slide range for gesture navigation"
        slides = window.Presentation.Slides
        first = 1 if int(settings.RangeType) == 1 else int(settings.StartingSlide)
        last = int(slides.Count) if int(settings.RangeType) == 1 else int(settings.EndingSlide)
        # Hidden trailing/leading slides must not let Next() unexpectedly exit.
        while last > first and bool(slides.Item(last).SlideShowTransition.Hidden):
            last -= 1
        while first < last and bool(slides.Item(first).SlideShowTransition.Hidden):
            first += 1
        if action == "next":
            if view.Slide.SlideIndex >= last:
                return "Already at last slide"
            if not self.guard():
                return "Focus or verification changed; action cancelled"
            view.Next()
        elif action == "previous":
            if view.Slide.SlideIndex <= first:
                return "Already at first slide"
            if not self.guard():
                return "Focus or verification changed; action cancelled"
            view.Previous()
        else:
            return "Action disabled"
        return f"PowerPoint: {action}"


class WordController:
    def __init__(self, app, window_match=None, guard=lambda: True):
        self.app, self.window_match = app, window_match or same_window
        self.guard = guard

    def execute(self, action: str, hwnd: int) -> str:
        if self.app.Documents.Count == 0:
            return "No Word document is open"
        win = self.app.ActiveWindow
        if not self.window_match(int(win.Hwnd), hwnd) or not self.guard():
            return "Word focus changed; action cancelled"
        if action not in {"next", "previous"}:
            return "Action disabled"
        if win.View.Type in {3, 6}:  # Print Layout or Web Layout
            # PageScroll does not change insertion point or selection.
            pane = win.ActivePane
            if not self.guard():
                return "Focus or verification changed; action cancelled"
            pane.PageScroll(**({"Down": 1} if action == "next" else {"Up": 1}))
        elif win.View.Type == 7:  # Reading view: scroll one visible screen
            if not self.guard():
                return "Focus or verification changed; action cancelled"
            win.LargeScroll(**({"Down": 1} if action == "next" else {"Up": 1}))
            return "Word Read Mode: one screen (layout-dependent)"
        else:
            return "Select Word Print Layout for page navigation"
        return f"Word: {action} page (cursor preserved)"


class OfficeWorker:
    """STA-owned COM, bounded dispatch and process-aware Office discovery."""
    def __init__(self, permit=lambda: False, get_foreground=foreground, *, state=None,
                 get_inventory=inventory, get_window=window_info, on_result=None, authorize_target=None,
                 allow_command=None, get_slide_rect=slideshow_client_rect):
        self.permit = permit
        self.on_result = on_result
        self.authorize_target = authorize_target or (lambda app: True)
        self.allow_command = allow_command or (lambda app, command: True)
        self.get_slide_rect = get_slide_rect
        self._slides = ()
        self._slides_at = 0.
        self._slides_known = False
        self._show_sessions = {}
        self.get_foreground = get_foreground
        self.state = state
        self.get_inventory, self.get_window = get_inventory, get_window
        self._lock = threading.RLock()
        self._target = Target()
        self._status = "Office worker starting"
        self.details = {}
        self.paused_word = False
        self.commands = queue.Queue(maxsize=1)
        self.stop_event = threading.Event()
        self.thread = None
        self.last_handled_event = -1e9
        self.last_discovery = None

    @property
    def target(self):
        with self._lock:
            return self._target

    def slide_snapshot(self):
        with self._lock:
            return self._slides, self._slides_at, self._slides_known

    def refresh_slides(self, getter, now=None):
        """STA-only read of show/slide identity; no presentation modifications."""
        now = time.monotonic() if now is None else now
        contexts, sessions = [], {}
        try:
            if self.details.get("PowerPoint_running"):
                app = getter("PowerPoint.Application")
                windows = app.SlideShowWindows
                for i in range(1, int(windows.Count) + 1):
                    win = windows.Item(i)
                    hwnd = int(win.HWND)
                    presentation = win.Presentation
                    fingerprint = hashlib.sha256(str(presentation.FullName).encode()).hexdigest()
                    identity = (hwnd, fingerprint)
                    session = self._show_sessions.get(identity, uuid.uuid4().hex)
                    sessions[identity] = session
                    slide = win.View.Slide
                    # Embedded presentations may reuse SlideIDs from the parent.
                    deck = hashlib.sha256(str(slide.Parent.FullName).encode()).hexdigest()[:16]
                    size = presentation.PageSetup
                    rect = fit_slide(self.get_slide_rect(hwnd), float(size.SlideWidth) / float(size.SlideHeight))
                    contexts.append(SlideContext(session, f"{deck}:{int(slide.SlideID)}", hwnd, rect, now,
                                                 int(win.View.State) in {1, 2}))
            with self._lock:
                self._show_sessions = sessions
                self._slides, self._slides_at, self._slides_known = tuple(contexts), now, True
            self.publish(writing_available=bool(contexts), writing_error="")
        except Exception as exc:
            # Busy COM must never keep accepting input against an old slide.
            with self._lock:
                self._slides_known = False
            self.publish(writing_available=False,
                         writing_error=f"Air Writing is waiting for slideshow information ({type(exc).__name__})")

    def current_slide(self, now=None):
        now = time.monotonic() if now is None else now
        contexts, observed, known = self.slide_snapshot()
        target = self.target
        if not known or now - observed > .25 or target.app != "PowerPoint" or not target.ready:
            return None
        if self.get_foreground()[0] != (target.focus_hwnd or target.hwnd):
            return None
        return next((c for c in contexts if c.hwnd == target.hwnd and c.visible), None)

    @target.setter
    def target(self, value):
        with self._lock:
            self._target = value

    @property
    def status(self):
        with self._lock:
            return self._status

    @status.setter
    def status(self, value):
        with self._lock:
            self._status = value

    def publish(self, **values):
        if self.state:
            self.state.update_office(**values)

    def result(self, result, *, rejected=False, event=None):
        self.status = result
        self.publish(command_result=result, rejection_reason=result if rejected else "")
        if self.state:
            self.state.update_gesture(execution_result=result, rejection_reason=result if rejected else "")
        log.info("office_command result=%s rejected=%s", result, rejected)
        if self.on_result:
            # Whitelist actual controller confirmations. A detected or queued
            # gesture, a clamped slide boundary, and a disabled action cannot
            # become successful-action feedback.
            confirmed = result in {
                "PowerPoint: next", "PowerPoint: previous",
                "Exited slideshow; presentation remains open",
                "Word: next page (cursor preserved)", "Word: previous page (cursor preserved)",
                "Word Read Mode: one screen (layout-dependent)",
                "Word gestures paused", "Word gestures resumed",
            }
            self.on_result({"result": result, "success": confirmed and not rejected,
                            "gesture": event.gesture if isinstance(event, GestureEvent) else "",
                            "command": event.command if isinstance(event, GestureEvent) else "",
                            "app": event.target.app if isinstance(event, GestureEvent) else "",
                            "timestamp": time.monotonic()})
        return result

    def start(self):
        self.thread = threading.Thread(target=self._run, name="office-com", daemon=True)
        self.thread.start()

    def submit(self, gesture, mappings=None):
        if isinstance(gesture, GestureEvent):
            item = gesture
        else:
            target = self.target
            action = (mappings or {}).get(target.app, {}).get(str(gesture), "none")
            epoch = self.state.snapshot().auth_epoch if self.state else 0
            item = GestureEvent(str(gesture), "", 0., time.monotonic(), True,
                                self.permit(), epoch, target, action)
        try:
            self.commands.put_nowait(item)
            if self.state:
                self.state.update_gesture(command_dispatched=True, intended_command=item.command)
            return True
        except queue.Full:
            self.result("Rejected: Office command queue full", rejected=True, event=item)
            return False

    def detect(self, getter) -> Target:
        hwnd, cls = self.get_foreground()
        inv = self.get_inventory()
        info = self.get_window(hwnd, inv.get("processes", {}))
        ppt_running, word_running = bool(inv["PowerPoint"]), bool(inv["Word"])
        ppt_foreground = info.exe.upper() == "POWERPNT.EXE"
        word_foreground = info.exe.upper() == "WINWORD.EXE"
        details = {"PowerPoint_installed": inv.get("installed", {}).get("PowerPoint", False),
                   "Word_installed": inv.get("installed", {}).get("Word", False),
                   "PowerPoint_running": ppt_running, "Word_running": word_running,
                   "foreground_exe": info.exe, "foreground_hwnd": hwnd, "foreground_class": cls,
                   "active_slideshow": False, "slideshow_count": 0,
                   "PowerPoint_COM": "not running", "Word_COM": "not running"}
        target = Target()
        ppt_target = None
        if ppt_running:
            try:
                app = getter("PowerPoint.Application")
                count = int(app.SlideShowWindows.Count)
                details.update(PowerPoint_COM="connected", active_slideshow=count > 0, slideshow_count=count)
                ppt_target = Target("PowerPoint", 0, "PowerPoint Running — No Active Slideshow", "editor")
                if count:
                    windows = [app.SlideShowWindows.Item(i) for i in range(1, count + 1)]
                    active = next((win for win in windows if same_window(int(win.HWND), hwnd)), None)
                    # A single slideshow on another monitor can be controlled
                    # from a foreground PowerPoint presenter/editor window.
                    if active is None and count == 1 and ppt_foreground:
                        active = windows[0]
                    if active is not None and ppt_foreground:
                        ppt_target = Target("PowerPoint", int(active.HWND), "Slideshow ready", "slideshow", hwnd)
                    else:
                        ppt_target = Target("PowerPoint", 0,
                            "PowerPoint Slideshow Active — Focus PowerPoint" if count == 1
                            else "Multiple slideshows — Focus the intended slideshow", "background")
            except Exception as exc:
                details["PowerPoint_COM"] = f"unavailable ({type(exc).__name__}); retrying"
                ppt_target = Target("PowerPoint", 0, "PowerPoint Running — COM unavailable; retrying", "unavailable")
        word_target = None
        if word_running:
            try:
                app = getter("Word.Application")
                count = int(app.Documents.Count)
                details["Word_COM"] = "connected"
                if word_foreground and count:
                    actual = int(app.ActiveWindow.Hwnd)
                    word_target = (Target("Word", actual, "Word ready", "document", hwnd)
                                   if same_window(actual, hwnd)
                                   else Target("Word", 0, "Focus the Word document window", "background"))
                else:
                    word_target = Target("Word", 0, "Word Running — Focus a document" if count else "Word Running — No Open Document", "background")
            except Exception as exc:
                details["Word_COM"] = f"unavailable ({type(exc).__name__}); retrying"
                word_target = Target("Word", 0, "Word Running — COM unavailable; retrying", "unavailable")
        target = ((ppt_target if ppt_foreground else word_target if word_foreground else None)
                  or ppt_target or word_target or target)
        self.details = details
        discovery = (target.app, target.kind, target.status, ppt_running, word_running,
                     details["PowerPoint_COM"], details["Word_COM"])
        if discovery != self.last_discovery:
            log.info("office_discovery target=%s mode=%s ppt_running=%s word_running=%s ppt_com=%s word_com=%s status=%s",
                     target.app, target.kind, ppt_running, word_running,
                     details["PowerPoint_COM"], details["Word_COM"], target.status)
            self.last_discovery = discovery
        self.publish(**details, target=target.app, target_status=target.status, target_ready=target.ready)
        return target

    def dispatch(self, item, getter, now):
        def report(message, **kwargs):
            return self.result(message, event=item, **kwargs)

        snapshot = self.state.snapshot() if self.state else None
        if snapshot and snapshot.identity_decision is not None:
            if not isinstance(item, GestureEvent) or not snapshot.identity_user or item.user_id != snapshot.identity_user["user_id"]:
                return report("Access denied: registered identity changed", rejected=True)
        # Tuple support is kept only for callers of the original single-owner API.
        if isinstance(item, GestureEvent):
            expected, action, issued = item.target, item.command, item.timestamp
            if not expected.ready:
                return report("Rejected: no ready Office target", rejected=True)
            threshold = self.state.settings.gesture_threshold if self.state else .8
            if not threshold <= item.confidence <= 1:
                return report("Rejected: gesture confidence below threshold or invalid", rejected=True)
            if item.gesture in {"LEFT_L", "RIGHT_L"} and item.hand != ("Left" if item.gesture == "LEFT_L" else "Right"):
                return report("Rejected: inconsistent anatomical hand", rejected=True)
            if not item.stable:
                return report("Rejected: gesture not stabilized", rejected=True)
            if not item.authorized:
                return report("Rejected: gesture captured while unverified", rejected=True)
            if self.state and item.auth_epoch != self.state.snapshot().auth_epoch:
                return report("Rejected: authentication changed after gesture", rejected=True)
            if item.timestamp <= self.last_handled_event:
                return report("Rejected: duplicate or out-of-order gesture event", rejected=True)
        else:
            expected, action, issued = item
        allowed = {"PowerPoint": {"previous", "next", "exit"}, "Word": {"previous", "next", "pause", "toggle"}}
        if action not in allowed.get(expected.app, set()):
            return report("Rejected: requested action disabled or invalid", rejected=True)
        if not self.allow_command(expected.app, action):
            return report("Writing Mode ON; slide navigation suppressed", rejected=True)
        if now - issued > .8 or issued > now:
            return report("Action cancelled: stale command", rejected=True)
        if not self.permit():
            reason = self.state.denial_reason() if self.state else "unverified, paused or gestures disabled"
            return report(f"Action cancelled: {reason or 'session or worker unavailable'}", rejected=True)
        if not self.authorize_target(expected.app) or (self.state and not self.state.permit(app=expected.app)):
            return report(f"Access denied: {expected.app} permission unavailable", rejected=True)
        hwnd, _ = self.get_foreground()
        if expected.key != self.target.key or hwnd != (expected.focus_hwnd or expected.hwnd):
            return report("Application changed; action cancelled", rejected=True)
        if isinstance(item, GestureEvent):
            self.last_handled_event = issued
        guard = lambda: (self.permit() and self.authorize_target(expected.app) and self.allow_command(expected.app, action)
                         and (not self.state or (self.state.permit(app=expected.app) and
                              (not isinstance(item, GestureEvent) or item.auth_epoch == self.state.snapshot().auth_epoch)))
                         and self.get_foreground()[0] == hwnd
                         and time.monotonic() - issued <= .8)
        if expected.app == "Word":
            if action == "toggle":
                self.paused_word = not self.paused_word
            elif action == "pause":
                self.paused_word = True
            elif not self.paused_word:
                result = WordController(getter("Word.Application"), guard=guard).execute(action, expected.hwnd)
                return report(result, rejected="cancelled" in result or "Select " in result)
            self.publish(Word_control_paused=self.paused_word)
            return report("Word gestures paused" if self.paused_word else "Word gestures resumed")
        if expected.app == "PowerPoint":
            result = PowerPointController(getter("PowerPoint.Application"), guard=guard).execute(action, expected.hwnd)
            return report(result, rejected=any(word in result for word in ("No foreground", "cancelled", "require")))
        return report("Unsupported application", rejected=True)

    def _run(self):
        initialized = False
        try:
            import pythoncom
            import win32com.client
            pythoncom.CoInitializeEx(pythoncom.COINIT_APARTMENTTHREADED)
            initialized = True
            getter = win32com.client.GetActiveObject
            next_poll = next_slide_poll = 0.
            while not self.stop_event.is_set():
                now = time.monotonic()
                if now >= next_poll:
                    try:
                        self.target = self.detect(getter)
                        self.status = self.target.status
                    except Exception:
                        # Inventory failures are reported, never converted into
                        # silently working Office targets.
                        self.target = Target(status="Office discovery unavailable; retrying")
                        self.status = self.target.status
                        self.publish(discovery_error=self.status, target="None", target_ready=False)
                    next_poll = now + .35
                if now >= next_slide_poll:
                    self.refresh_slides(getter, now)
                    next_slide_poll = now + .08
                try:
                    item = self.commands.get(timeout=.05)
                except queue.Empty:
                    continue
                try:
                    # Re-read process/window/COM state immediately before use.
                    self.target = self.detect(getter)
                    self.status = self.dispatch(item, getter, time.monotonic())
                    self.refresh_slides(getter)
                except Exception as exc:
                    self.result(f"Office command failed: {type(exc).__name__}; retry after dismissing Office dialogs", rejected=True, event=item)
                pythoncom.PumpWaitingMessages()
        except Exception:
            self.status = "Office worker failed; restart VisionGuard"
            self.publish(worker_error=self.status, target_ready=False)
            log.exception("Office worker failed")
        finally:
            if initialized:
                pythoncom.CoUninitialize()

    def stop(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=2)
            if self.thread.is_alive():
                log.warning("office_shutdown_timeout COM call still blocked; process restart may be needed")
