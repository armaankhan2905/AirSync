"""One synchronized, versioned state for authentication, GUI and dispatch."""
from copy import deepcopy
from dataclasses import dataclass, field, replace
import logging
import threading
import time
from .security import Decision, Observation, PresenceMonitor, State

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class AppSnapshot:
    revision: int = 0
    auth_epoch: int = 0
    observed_at: float = 0.
    captured_at: float = -1.
    decision: Decision = field(default_factory=lambda: Decision(State.UNENROLLED))
    identity_decision: Decision | None = None
    identity_user: dict | None = None
    directory_revision: int = -1
    last_verified: float | None = None
    face_count: int = 0
    face_status: str = "not_started"
    similarity: float | None = None
    threshold: float = .53
    camera: str = "Not started"
    prompt: str = "Complete setup"
    enrolled: bool = False
    paused: bool = False
    locked: bool = False
    enrolling: bool = False
    gestures_enabled: bool = True
    office: dict = field(default_factory=dict)
    gesture: dict = field(default_factory=dict)
    performance: dict = field(default_factory=dict)


class StateManager:
    def __init__(self, settings):
        self.lock = threading.RLock()
        self.settings = settings
        self.presence = PresenceMonitor(settings)
        self.identity_presence = PresenceMonitor(replace(settings, auto_lock=False, strict_privacy=False))
        self.identity_candidate = None
        self._snapshot = AppSnapshot(gestures_enabled=settings.gestures_enabled)

    def snapshot(self):
        with self.lock:
            return deepcopy(self._snapshot)

    def configure(self, settings):
        with self.lock:
            revoked = self._snapshot.gestures_enabled and not settings.gestures_enabled
            self.settings = settings
            self.presence.settings = settings
            self.identity_presence.settings = replace(settings, auto_lock=False, strict_privacy=False)
            self._set(gestures_enabled=settings.gestures_enabled,
                      auth_epoch=self._snapshot.auth_epoch + int(revoked))

    def _set(self, **changes):
        self._snapshot = replace(self._snapshot, revision=self._snapshot.revision + 1, **changes)
        return self._snapshot

    def context(self, *, reason, preserve_presence=False, **changes):
        """Invalidate outstanding commands before a pause/session/profile change."""
        with self.lock:
            if not preserve_presence:
                self.presence.reset()
            self.identity_presence.reset()
            self.identity_candidate = None
            paused = changes.get("paused", self._snapshot.paused)
            locked = changes.get("locked", self._snapshot.locked)
            enrolled = changes.get("enrolled", self._snapshot.enrolled)
            state = State.LOCKED if locked else State.PAUSED if paused else State.VERIFYING if enrolled else State.UNENROLLED
            self._set(**changes, auth_epoch=self._snapshot.auth_epoch + 1,
                      decision=self._snapshot.decision if preserve_presence else Decision(state, reason=reason),
                      identity_decision=Decision(state, reason=reason) if self._snapshot.identity_decision is not None else None,
                      identity_user=None, prompt=reason,
                      last_verified=self._snapshot.last_verified if preserve_presence else None,
                      captured_at=time.monotonic())

    def update_auth(self, observation: Observation, now: float, *, captured_at=None,
                    face_count=0, similarity=None, threshold=None, prompt="", camera="Connected",
                    enrolled=True, identity_observation=None, identity_user=None, directory_revision=None):
        with self.lock:
            old = self._snapshot
            captured_at = now if captured_at is None else captured_at
            # A watchdog/error observation also advances the watermark. A late
            # inference result must never replace that newer security decision.
            if captured_at <= old.captured_at:
                return old, False
            if now - captured_at > .8:
                observation = Observation(camera_ok=False, kind="stale")
                camera, prompt = "Inference delayed", "Discarded stale recognition result"
            decision = self.presence.update(observation, now, enrolled=enrolled,
                                            locked=old.locked, paused=old.paused or old.enrolling)
            identity_decision = None
            identity_changes = {}
            if identity_observation is not None or old.identity_decision is not None:
                identity_observation = identity_observation or observation
                if now - captured_at > .8:
                    identity_observation = Observation(camera_ok=False, kind="stale")
                if identity_user is None and identity_observation.kind in {"quality", "absent", "uncertain"}:
                    identity_user = old.identity_user
                candidate = identity_user.get("user_id") if identity_user else None
                if candidate != self.identity_candidate:
                    self.identity_presence.reset()
                    self.identity_candidate = candidate
                    identity_changes["changed"] = True
                identity_decision = self.identity_presence.update(identity_observation, now, enrolled=enrolled,
                    locked=old.locked, paused=old.paused or old.enrolling)
                if not identity_decision.controls_allowed and not identity_observation.authorized:
                    identity_user = None
            old_auth = old.identity_decision or old.decision
            new_auth = identity_decision or decision
            revoked = (old_auth.controls_allowed and not new_auth.controls_allowed) or bool(identity_changes)
            last = self.presence.last_verified_at
            result = self._set(observed_at=now, captured_at=captured_at, decision=decision,
                               identity_decision=identity_decision, identity_user=deepcopy(identity_user),
                               directory_revision=old.directory_revision if directory_revision is None else directory_revision,
                               auth_epoch=old.auth_epoch + int(revoked), last_verified=last,
                               face_count=face_count, face_status=identity_observation.kind if identity_decision is not None else observation.kind,
                               similarity=similarity, threshold=threshold if threshold is not None else old.threshold,
                               prompt=prompt or decision.reason, camera=camera, enrolled=enrolled)
            if old.decision.state != decision.state or old.decision.in_grace != decision.in_grace:
                log.info("auth_transition state=%s grace=%s reason=%s score=%s threshold=%.3f revision=%d",
                         decision.state, decision.in_grace, decision.reason,
                         None if similarity is None else round(similarity, 3), result.threshold, result.revision)
            return result, True

    def permit(self, now=None, app=None):
        return not self.denial_reason(now, app)

    def denial_reason(self, now=None, app=None):
        now = time.monotonic() if now is None else now
        with self.lock:
            s = self._snapshot
            if not s.gestures_enabled:
                return "Global gesture control disabled"
            if s.paused:
                return "Monitoring paused"
            if s.locked:
                return "Windows session locked"
            if s.enrolling:
                return "Face enrollment in progress"
            if s.face_count > 1:
                return "Additional face visible; hand ownership uncertain"
            if s.identity_decision is not None:
                if s.face_count != 1:
                    return "No registered user visible"
                if s.decision.state in {State.LOCKED, State.LOCK_PENDING}:
                    return "Workstation locking pending"
                if not s.identity_decision.controls_allowed or not s.identity_user:
                    return "Registered user not verified"
                p = s.identity_user["permissions"]
                if not s.identity_user["is_active"] or not p["can_use_gestures"]:
                    return "User gesture permission denied"
                permission = {"PowerPoint": "can_control_powerpoint", "Word": "can_control_word"}.get(app)
                if permission and not p[permission]:
                    return f"{app} permission denied"
            elif not s.decision.controls_allowed:
                return s.decision.reason or "Owner not verified"
            if now - s.observed_at > .8:
                return "Authentication result stale"
            return ""

    def update_office(self, **values):
        with self.lock:
            self._set(office={**self._snapshot.office, **deepcopy(values)})

    def update_gesture(self, **values):
        with self.lock:
            self._set(gesture={**self._snapshot.gesture, **deepcopy(values)})

    def update_performance(self, **values):
        with self.lock:
            self._set(performance={**self._snapshot.performance, **values})

    def diagnostics(self, now=None):
        now = time.monotonic() if now is None else now
        with self.lock:
            s = self._snapshot
            missing = self.presence.missing_since
            identity = s.identity_decision or s.decision
            last_identity = self.identity_presence.last_verified_at if s.identity_decision is not None else s.last_verified
            return {"revision": s.revision, "auth_epoch": s.auth_epoch,
                    "face": {"detected": s.face_count > 0, "count": s.face_count,
                             "observation": s.face_status, "verified": identity.controls_allowed,
                             "similarity": s.similarity, "acquire_threshold": self.settings.recognition_threshold + self.settings.recognition_margin,
                             "current_threshold": s.threshold, "in_grace": identity.in_grace,
                             "last_verified_monotonic": last_identity,
                             "seconds_since_verified": None if last_identity is None else max(0, now - last_identity),
                             "state": str(identity.state), "reason": identity.reason},
                    "identity_user": deepcopy(s.identity_user), "directory_revision": s.directory_revision,
                    "office": deepcopy(s.office), "gesture": deepcopy(s.gesture),
                    "security": {"auto_lock": self.settings.auto_lock,
                                 "trusted_presence_verified": s.decision.controls_allowed,
                                 "workstation_state": str(s.decision.state),
                                 "simulation": self.settings.development_simulation,
                                 "absence_seconds": 0 if missing is None else max(0, now - missing),
                                 "countdown": s.decision.remaining, "camera": s.camera},
                    "performance": deepcopy(s.performance)}
