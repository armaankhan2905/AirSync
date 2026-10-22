"""Deterministic presence policy and safe workstation integration."""
from dataclasses import dataclass
from enum import StrEnum
import ctypes
import logging
import sys
from .config import Settings


class State(StrEnum):
    UNENROLLED = "UNENROLLED"
    VERIFYING = "VERIFYING"
    VERIFIED = "VERIFIED"
    TEMPORARILY_MISSING = "TEMPORARILY_MISSING"
    UNKNOWN_PERSON = "UNKNOWN_PERSON"
    LOCK_PENDING = "LOCK_PENDING"
    LOCKED = "LOCKED"
    CAMERA_ERROR = "CAMERA_ERROR"
    RECOGNITION_ERROR = "RECOGNITION_ERROR"
    PAUSED = "PAUSED"


@dataclass(frozen=True)
class Observation:
    authorized: bool = False
    unknown_count: int = 0
    camera_ok: bool = True
    kind: str = "absent"


@dataclass(frozen=True)
class Decision:
    state: State
    remaining: float | None = None
    lock: bool = False
    controls_allowed: bool = False
    reason: str = ""
    in_grace: bool = False


class PresenceMonitor:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.missing_since = None
        self.consecutive = 0
        self.lock_requested = False
        self.last_verified_at = None
        self.retaining = False

    def reset(self):
        self.missing_since = None
        self.consecutive = 0
        self.lock_requested = False
        self.last_verified_at = None
        self.retaining = False

    def update(self, observation: Observation, now: float, *, enrolled=True,
               locked=False, paused=False) -> Decision:
        if locked:
            self.consecutive = 0
            return Decision(State.LOCKED)
        if not enrolled or paused:
            self.reset()
            return Decision(State.PAUSED if paused else State.UNENROLLED)
        if self.lock_requested:
            return Decision(State.LOCK_PENDING, 0)
        valid = (observation.camera_ok and observation.authorized and
                 not (self.settings.strict_privacy and observation.unknown_count))
        self.consecutive = self.consecutive + 1 if valid else 0
        if valid and self.consecutive >= self.settings.verification_frames:
            self.missing_since = None
            self.last_verified_at = now
            self.retaining = True
            return Decision(State.VERIFIED, controls_allowed=True, reason="Consecutive owner matches and liveness passed")
        if self.missing_since is None:
            self.missing_since = now
        remaining = max(0., self.settings.absence_timeout - (now - self.missing_since))
        can_lock = self.settings.auto_lock and (observation.camera_ok or self.settings.camera_policy == "lock")
        if can_lock and remaining <= 0:
            self.lock_requested = True
            self.retaining = False
            return Decision(State.LOCK_PENDING, 0, lock=True, reason="Authorized owner absent for configured timeout")
        within_grace = (self.retaining and self.last_verified_at is not None
                        and now - self.last_verified_at <= self.settings.verification_grace)
        uncertain = (observation.camera_ok and not observation.unknown_count
                     and observation.kind in {"absent", "quality", "uncertain", "matched"})
        if within_grace and uncertain:
            if valid:
                # Do not let isolated returning matches indefinitely postpone a
                # genuine absence: only a full consecutive sequence renews time.
                reason = "Owner returning within verification grace"
            else:
                reason = f"Brief {observation.kind} observation; retaining recent verified identity"
            return Decision(State.VERIFIED, remaining if can_lock else None,
                            controls_allowed=True, reason=reason, in_grace=True)
        self.retaining = False
        if observation.kind == "recognition_error":
            state = State.RECOGNITION_ERROR
        elif not observation.camera_ok:
            state = State.CAMERA_ERROR
        elif observation.unknown_count:
            state = State.UNKNOWN_PERSON
        elif valid:
            state = State.VERIFYING
        else:
            state = State.TEMPORARILY_MISSING
        return Decision(state, remaining if can_lock else None,
                        reason={State.CAMERA_ERROR: "Camera or inference unavailable",
                                State.RECOGNITION_ERROR: "Face recognition model failed; camera may still be healthy",
                                State.UNKNOWN_PERSON: "Nonmatching face detected",
                                State.VERIFYING: "Waiting for consecutive owner observations"}.get(state, observation.kind))

    def lock_failed(self):
        self.lock_requested = False


class LockManager:
    def __init__(self, simulation: bool = True, lock_api=None):
        self.simulation = simulation
        self.lock_api = lock_api

    def lock(self) -> bool:
        if self.simulation:
            logging.getLogger(__name__).warning("SIMULATION: workstation lock requested")
            return True
        if self.lock_api is not None:
            if not self.lock_api():
                raise RuntimeError("Workstation lock API rejected the request")
            return True
        if sys.platform != "win32":
            raise RuntimeError("Workstation locking requires Windows")
        api = ctypes.WinDLL("user32", use_last_error=True).LockWorkStation
        api.argtypes = []
        api.restype = ctypes.c_int  # Win32 BOOL is a 32-bit integer
        if not api():
            raise ctypes.WinError(ctypes.get_last_error())
        return True


def session_locked() -> bool:
    """Default input desktop is unavailable while secure desktop is active.

    Access failure is conservative: disable controls until the desktop returns.
    Never calls SwitchDesktop or attempts to unlock.
    """
    if sys.platform != "win32":
        return False
    user = ctypes.WinDLL("user32", use_last_error=True)
    user.OpenInputDesktop.argtypes = [ctypes.c_uint, ctypes.c_int, ctypes.c_uint]
    user.OpenInputDesktop.restype = ctypes.c_void_p
    user.CloseDesktop.argtypes = [ctypes.c_void_p]
    user.GetUserObjectInformationW.argtypes = [ctypes.c_void_p, ctypes.c_int,
                                              ctypes.c_void_p, ctypes.c_uint, ctypes.POINTER(ctypes.c_uint)]
    handle = user.OpenInputDesktop(0, False, 1)
    if not handle:
        return True
    try:
        name = ctypes.create_unicode_buffer(256)
        needed = ctypes.c_uint()
        if not user.GetUserObjectInformationW(handle, 2, name, ctypes.sizeof(name), ctypes.byref(needed)):
            return True
        return name.value.casefold() != "default"
    finally:
        user.CloseDesktop(handle)
