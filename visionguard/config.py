"""Validated configuration; persisted only through encrypted storage."""
from dataclasses import asdict, dataclass, field
import os
from pathlib import Path
import sys


def data_dir() -> Path:
    return Path(os.environ.get("LOCALAPPDATA", Path.home() / ".local/share")) / "VisionGuardAI"


def model_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS) / "models"
    return Path(__file__).resolve().parent.parent / "models"


def default_mappings() -> dict:
    return {
        "PowerPoint": {"LEFT_L": "previous", "RIGHT_L": "next", "FIST": "exit", "PINCH": "exit"},
        "Word": {"LEFT_L": "previous", "RIGHT_L": "next", "FIST": "pause", "PINCH": "toggle"},
    }


@dataclass
class Settings:
    camera_consent: bool = False
    camera_index: int = 0
    auto_lock: bool = False
    absence_timeout: float = 5.0
    camera_policy: str = "lock"  # lock after timeout, or alert and suspend gestures
    strict_privacy: bool = False
    recognition_threshold: float = 0.50
    recognition_margin: float = 0.03
    verification_frames: int = 3
    verification_grace: float = 1.2
    recognition_hysteresis: float = 0.03
    developer_diagnostics: bool = False
    gestures_enabled: bool = True
    gesture_threshold: float = 0.80
    gesture_cooldown: float = 0.7
    gesture_frames: int = 4
    pinch_ratio: float = 0.23
    swap_hands: bool = False
    preview_visible: bool = True
    overlays: bool = True
    minimize_to_tray: bool = True
    launch_background: bool = False
    start_with_windows: bool = False
    notifications: bool = True
    development_simulation: bool = True
    mappings: dict = field(default_factory=default_mappings)

    def validate(self) -> "Settings":
        for key, lo, hi in [
            ("camera_index", 0, 15), ("absence_timeout", 2, 120),
            ("recognition_threshold", .4, .9), ("recognition_margin", .01, .1),
            ("verification_frames", 2, 10), ("gesture_threshold", .6, .99),
            ("verification_grace", 0, 3), ("recognition_hysteresis", 0, .08),
            ("gesture_cooldown", .3, 5), ("gesture_frames", 3, 15),
            ("pinch_ratio", .1, .4),
        ]:
            value = getattr(self, key)
            if not isinstance(value, (int, float)) or not lo <= value <= hi:
                raise ValueError(f"Invalid {key}")
        for key in ("camera_index", "verification_frames", "gesture_frames"):
            if type(getattr(self, key)) is not int:
                raise ValueError(f"{key} must be an integer")
        if self.verification_grace >= self.absence_timeout:
            raise ValueError("Verification grace must be shorter than the absence timeout")
        for key, value in asdict(self).items():
            if isinstance(getattr(Settings(), key), bool) and type(value) is not bool:
                raise ValueError(f"{key} must be boolean")
        if self.camera_policy not in {"lock", "alert"}:
            raise ValueError("Invalid camera failure policy")
        allowed = {"PowerPoint": {"previous", "next", "exit", "none"},
                   "Word": {"previous", "next", "pause", "toggle", "none"}}
        for app, mapping in self.mappings.items():
            if app not in allowed or set(mapping) != set(default_mappings()[app]):
                raise ValueError("Invalid application mapping")
            if any(action not in allowed[app] for action in mapping.values()):
                raise ValueError("Invalid action mapping")
        if set(self.mappings) != set(allowed):
            raise ValueError("Missing application mappings")
        return self
