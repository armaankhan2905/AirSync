"""Capture/hand orchestration with independent face inference and shared state."""
from copy import deepcopy
from dataclasses import replace
import logging
import os
import queue
import threading
import time
import uuid
import cv2
from PySide6.QtCore import QThread, Signal
from PySide6.QtGui import QImage
from .authentication import Enrollment, FaceEngine, FaceMatcher, LivenessChallenge
from .camera import CameraManager
from .config import model_dir
from .face_worker import FaceInferenceWorker
from .gestures import Gesture, GestureStabilizer, HandTracker
from .office import GestureEvent, OfficeWorker
from .models import verify_models
from .security import LockManager, Observation, State, session_locked
from .state import StateManager
from .recognition import RecognitionDirectory, public_user
from .users import RegistrationError

log = logging.getLogger(__name__)


class VisionWorker(QThread):
    image = Signal(QImage)
    status = Signal(dict)
    command_result = Signal(dict)
    enrollment_done = Signal()
    registration_result = Signal(dict)
    event = Signal(str)

    def __init__(self, settings, database, simulation=True):
        super().__init__()
        self.settings = deepcopy(settings)
        self.database = database
        self.worker_id = uuid.uuid4().hex
        self.stop_event = threading.Event()
        self.commands = queue.Queue(maxsize=32)
        self.state = StateManager(self.settings)
        self.lock_manager = LockManager(simulation)
        self.last_progress = time.monotonic()
        self.session_is_locked = False
        self.paused = False
        self.enrolling = None
        self.office = OfficeWorker(permit=self.permit, state=self.state, on_result=self.publish_command_result,
                                   authorize_target=lambda app: self.permit(app))
        self.multi_user = database.users.has_users() or not len(database.embeddings())
        self.directory = RecognitionDirectory(database.users, self.settings)
        self.registration_request = None
        self.registration_preview = False
        self.registration_cancelled = threading.Event()
        self._last_identity_event = None
        self._last_identity_event_at = -1e9
        self.camera = None
        self.face_worker = None
        self.last_lock_attempt = -1e9
        self.enrolled = database.users.has_users() if self.multi_user else bool(len(database.embeddings()))
        self.health_error = "Starting inference"
        self.challenge = LivenessChallenge(self.settings.verification_grace)
        self.visible_faces_count = 0
        self._image_lock = threading.Lock()
        self._latest_image = None
        self._last_log = -1e9
        self._last_rejection = ("", -1e9)

    def publish_command_result(self, feedback):
        self.command_result.emit({**feedback, "worker_id": self.worker_id})

    @property
    def decision(self):
        return self.state.snapshot().decision

    def permit(self, app=None):
        if session_locked():
            if not self.state.snapshot().locked:
                self.state.context(reason="Windows secure desktop active", locked=True)
            return False
        if not self.state.permit(app=app) or self.stop_event.is_set():
            return False
        snapshot = self.state.snapshot()
        if snapshot.identity_decision is not None:
            if snapshot.directory_revision != self.database.users.revision or not snapshot.identity_user:
                return False
            try:
                user = self.database.users.get(snapshot.identity_user["user_id"])
                return bool(user and user.is_active and user.permissions.can_use_gestures
                            and (app is None or user.permissions.allows(app)))
            except Exception:
                log.exception("Registered user authorization unavailable")
                return False
        return True

    def invalidate(self, reason, *, preserve_presence=False):
        self.state.context(reason=reason, preserve_presence=preserve_presence)

    def command(self, name, value=None):
        if name == "cancel_enroll":
            self.registration_cancelled.set()
        if name == "enroll" and self.multi_user:
            self.event.emit("Administrator-authorized named registration required")
            return
        if name in {"pause", "enroll", "reload", "session"}:
            self.invalidate(f"Applying {name} change")
        try:
            self.commands.put_nowait((name, deepcopy(value)))
        except queue.Full:
            self.event.emit("Worker command queue full; retry the operation")

    def tick_policy(self, observation, now, **values):
        if not observation.camera_ok:
            self.directory.tracks.clear()
        snapshot, accepted = self.state.update_auth(observation, now, enrolled=self.enrolled, **values)
        if accepted and snapshot.decision.lock and now - self.last_lock_attempt >= 2:
            self.last_lock_attempt = now
            try:
                self.lock_manager.lock()
                self.event.emit("SIMULATED workstation lock" if self.lock_manager.simulation else "Windows lock requested")
            except Exception:
                log.exception("Workstation locking failed")
                self.event.emit("Windows lock failed; check the application log")
                with self.state.lock:
                    self.state.presence.lock_failed()
        elif accepted and snapshot.decision.lock:
            with self.state.lock:
                self.state.presence.lock_failed()
        return snapshot.decision

    def watchdog(self):
        now = time.monotonic()
        locked = session_locked()
        if locked != self.session_is_locked:
            self.session_is_locked = locked
            self.state.context(reason="Windows session changed", locked=locked)
            self.command("session", locked)
        if now - self.last_progress > 2:
            self.tick_policy(Observation(camera_ok=False, kind="worker_stalled"), now,
                             camera="Worker stalled or unavailable", prompt=self.health_error or "Inference watchdog active")

    def _commands(self, matcher, stabilizer):
        while True:
            try:
                name, value = self.commands.get_nowait()
            except queue.Empty:
                return matcher, stabilizer
            if name == "settings":
                self.settings = value
                self.state.configure(value)
                self.lock_manager.simulation = value.development_simulation
                matcher.threshold, matcher.margin = value.recognition_threshold, value.recognition_margin
                self.challenge.grace = value.verification_grace
                self.directory.settings = value
                stabilizer = GestureStabilizer(value.gesture_frames, value.gesture_cooldown, value.gesture_threshold)
            elif name == "enroll":
                self.enrolling = Enrollment()
                self.state.context(reason="Enrollment in progress", enrolling=True)
                self.event.emit("Enrollment started; no raw face images will be saved")
            elif name == "register":
                try:
                    self.database.users.require_admin(value.get("token"))
                    self.database.users._name(value.get("name", ""))
                    if value.get("user_id") and self.database.users.get(value["user_id"]) is None:
                        raise RegistrationError("User no longer exists")
                    self.multi_user = True
                    self.registration_cancelled.clear()
                    self.registration_request = value
                    self.enrolling = Enrollment()
                    self.state.context(reason="Face registration in progress", enrolling=True)
                    self.event.emit("Registration started; follow the camera prompts")
                except (ValueError, PermissionError) as exc:
                    self.registration_result.emit({"worker_id": self.worker_id, "request_id": value.get("request_id"),
                                                   "success": False, "message": str(exc)})
            elif name == "cancel_enroll":
                self.enrolling = None
                self.registration_request = None
                self.directory.tracks.clear()
                self.state.context(reason="Enrollment cancelled", enrolling=False)
            elif name == "registration_preview":
                self.registration_preview = bool(value)
            elif name == "reload":
                if self.multi_user:
                    self.directory.tracks.clear()
                    self.refresh_directory()
                    continue
                samples = self.database.embeddings()
                self.enrolled = bool(len(samples))
                matcher = FaceMatcher(samples, self.settings.recognition_threshold, self.settings.recognition_margin)
                self.challenge.reset()
                self.state.context(reason="Enrollment reloaded", enrolled=self.enrolled)
            elif name == "pause":
                self.paused = bool(value)
                self.state.context(reason="Monitoring paused" if value else "Monitoring resumed", paused=self.paused)
                self.challenge.reset()
                self.directory.tracks.clear()
                stabilizer.reset()
            elif name == "session":
                self.challenge.reset()
                self.directory.tracks.clear()
                stabilizer.reset()

    def process_faces(self, result, matcher):
        """Consume one NEW result; never authorize using old preview boxes."""
        if self.multi_user:
            return self.process_registered_faces(result, matcher)
        now = time.monotonic()
        if result.captured_at <= self.state.snapshot().captured_at:
            return None
        self.state.update_performance(face_ms=round(result.elapsed_ms, 1),
                                      face_result_age_ms=round((now - result.captured_at) * 1000, 1))
        if result.error or now - result.captured_at > .8:
            self.challenge.reset()
            self.tick_policy(Observation(camera_ok=False, kind="recognition_error" if result.error else "stale"), now,
                             captured_at=result.captured_at, camera="Inference unavailable",
                             prompt=result.error or "Face inference delayed; stale result discarded")
            return [], False, matcher
        faces = result.faces
        self.visible_faces_count = len(faces)
        retaining = self.state.snapshot().decision.controls_allowed
        threshold = matcher.threshold + matcher.margin - (self.settings.recognition_hysteresis if retaining else 0)
        for face in faces:
            match, face.score = matcher.match(face.embedding, retaining=retaining,
                                              hysteresis=self.settings.recognition_hysteresis)
            face.matched = face.usable_for_verification and match
        matched = [face for face in faces if face.matched]
        unknown = sum(face.usable_for_verification and not face.matched for face in faces)
        live, kind, prompt = False, "absent", "Face out of view"
        if self.enrolling is not None:
            prompt = self.enrolling.add([replace(f, yaw=-f.yaw) for f in faces], now)
            kind = "enrollment"
            if prompt == "READY":
                self.database.enroll(self.enrolling.embeddings)
                matcher = FaceMatcher(self.enrolling.embeddings, self.settings.recognition_threshold, self.settings.recognition_margin)
                self.enrolled = True
                self.enrolling = None
                self.challenge.reset()
                self.state.context(reason="Enrollment complete; verify face", enrolling=False, enrolled=True)
                self.enrollment_done.emit()
                self.event.emit("Face enrollment securely saved")
                prompt = "Enrollment complete; follow verification prompts"
        else:
            face = matched[0] if len(matched) == 1 else None
            if face and len(faces) > 1 and now < self.challenge.valid_until:
                live = True
            elif len(faces) > 1:
                self.challenge.reset()
            else:
                live = self.challenge.update(-face.yaw if face and len(faces) == 1 else None, now,
                                             identity_ok=bool(face) or not unknown)
            if not self.enrolled:
                prompt, kind = "Select Enroll Face to get started", "unenrolled"
            elif len(faces) > 1:
                prompt, kind = "Additional face visible; Office gestures suspended", "multiple"
            elif not faces:
                prompt, kind = "Face out of view; look toward the camera to resume", "absent"
            elif not faces[0].usable_for_verification:
                prompt, kind = faces[0].reason, "quality"
            elif not faces[0].matched:
                prompt, kind = (f"Face similarity {faces[0].score:.2f}; required {threshold:.2f}. "
                                "Face the camera in even light; redo multi-angle enrollment if this persists"), "unknown"
            elif live:
                prompt, kind = "Owner verified", "matched"
            else:
                prompt, kind = self.challenge.prompt, "challenge"
        score = max((f.score for f in faces), default=None)
        decision = self.tick_policy(Observation(bool(matched) and live, unknown, kind=kind), now,
                                    captured_at=result.captured_at, face_count=len(faces), similarity=score,
                                    threshold=threshold, prompt=prompt)
        if now - self._last_log >= 2:
            log.info("face_observation kind=%s faces=%d score=%s threshold=%.3f inference_ms=%.1f age_ms=%.1f state=%s quality_reason=%s",
                     kind, len(faces), None if score is None else round(score, 3), threshold,
                     result.elapsed_ms, (now - result.captured_at) * 1000, decision.state,
                     "; ".join(f.reason for f in faces if not f.usable_for_verification) or "ok")
            self._last_log = now
        return faces, live, matcher

    def refresh_directory(self):
        if self.directory.revision != self.database.users.revision:
            self.directory.refresh()
            self.enrolled = self.database.users.has_users()
            self.state.context(reason="User directory updated", preserve_presence=True, enrolled=self.enrolled)
            return True
        self.enrolled = self.database.users.has_users()
        return False

    def process_registered_faces(self, result, matcher):
        now = time.monotonic()
        self.refresh_directory()
        if result.captured_at <= self.state.snapshot().captured_at:
            return None
        self.state.update_performance(face_ms=round(result.elapsed_ms, 1),
                                      face_result_age_ms=round((now - result.captured_at) * 1000, 1))
        if result.error or now - result.captured_at > .8:
            self.directory.tracks.clear()
            self.tick_policy(Observation(camera_ok=False, kind="recognition_error" if result.error else "stale"), now,
                identity_observation=Observation(camera_ok=False, kind="recognition_error"),
                directory_revision=self.directory.revision, captured_at=result.captured_at,
                camera="Inference unavailable", prompt="Waiting for fresh recognition")
            return [], False, matcher
        snapshot = self.state.snapshot()
        retained = snapshot.identity_user["user_id"] if snapshot.identity_user and snapshot.identity_decision and snapshot.identity_decision.controls_allowed else None
        faces = self.directory.observe(result.faces, now, retained_id=retained)
        self.visible_faces_count = len(faces)
        live, user = False, None
        kind, prompt = "absent", "Look at the camera to begin automatic recognition"
        if self.enrolling is not None:
            request = self.registration_request
            try:
                self.database.users.require_admin(request.get("token") if request else None)
                prompt = self.enrolling.add([replace(f, yaw=-f.yaw) for f in faces], now)
                kind = "enrollment"
                if prompt == "READY":
                    if self.registration_cancelled.is_set():
                        raise RegistrationError("Registration cancelled")
                    if request.get("user_id"):
                        self.database.users.reenroll(request["user_id"], self.enrolling.embeddings, request["token"])
                    else:
                        self.database.users.register(request["name"], self.enrolling.embeddings, request["token"])
                    self.enrolling, self.registration_request = None, None
                    self.refresh_directory()
                    self.state.context(reason="User registered; automatic recognition ready", enrolling=False,
                                       enrolled=True, preserve_presence=True)
                    self.registration_result.emit({"worker_id": self.worker_id, "success": True,
                                                   "request_id": request.get("request_id"),
                                                   "message": "User registered successfully."})
                    self.event.emit("User registered successfully.")
                    self.enrollment_done.emit()
                    # Registration does not itself grant live authorization.
                    return [], False, matcher
            except (PermissionError, RegistrationError) as exc:
                self.enrolling, self.registration_request = None, None
                self.state.context(reason="Registration stopped", enrolling=False, preserve_presence=True)
                self.registration_result.emit({"worker_id": self.worker_id, "request_id": request.get("request_id") if request else None,
                                               "success": False, "message": str(exc)})
                self.event.emit(str(exc))
                return [], False, matcher
        elif len(faces) > 1:
            kind, prompt = "multiple", "MULTIPLE PEOPLE — GESTURES PAUSED"
        elif faces:
            face = faces[0]
            user = self.directory.users.get(face.user_id, (None,))[0]
            live = face.live
            if not face.usable_for_verification:
                kind, prompt = "quality", face.reason
            elif face.identity_status == "uncertain":
                kind, prompt = "uncertain", "Identity uncertain · face the camera in even light"
            elif not user:
                kind, prompt = "unknown", "UNREGISTERED"
            elif not live:
                kind, prompt = "challenge", face.verification_prompt
            else:
                kind, prompt = "matched", f"Verified: {user.user_name}"
        elif not self.enrolled:
            kind, prompt = "unenrolled", "Register the device administrator to get started"
        unknown = sum(f.usable_for_verification and not f.matched for f in faces)
        # Gesture identity is independent of owner/trusted workstation presence.
        trusted = [f for f in faces if f.live and f.user_id in self.directory.users
                   and self.directory.users[f.user_id][0].permissions.trusted_presence]
        identity_observation = Observation(bool(user) and live and len(faces) == 1,
            max(unknown, 1 if kind == "uncertain" else 0), kind=kind)
        presence_kind = "matched" if trusted else "untrusted" if any(f.matched for f in faces) and kind != "quality" else kind
        decision = self.tick_policy(Observation(bool(trusted), unknown, kind=presence_kind), now,
            captured_at=result.captured_at, face_count=len(faces), similarity=max((f.score for f in faces), default=None),
            prompt=prompt, identity_observation=identity_observation, identity_user=public_user(user) if user else None,
            directory_revision=self.directory.revision)
        current = self.state.snapshot()
        identity = current.identity_decision
        audit = (current.identity_user["user_id"] if current.identity_user else None,
                 str(identity.state) if identity else "UNENROLLED")
        if audit != self._last_identity_event and (identity and identity.controls_allowed or kind == "unknown"):
            # Bounded audit history; noisy rejection transitions are rate limited.
            if now - self._last_identity_event_at >= 2 or audit[0] is not None:
                self.database.users.record_recognition(audit[0], "verified" if identity and identity.controls_allowed else "unregistered", audit[1])
                self._last_identity_event_at = now
            self._last_identity_event = audit
        if now - self._last_log >= 2:
            log.info("registered_observation kind=%s faces=%d identity=%s presence=%s revision=%d",
                     kind, len(faces), identity.state if identity else "none", decision.state, self.directory.revision)
            self._last_log = now
        return faces, live, matcher

    def dispatch_gesture(self, detections, stabilizer, now):
        target = self.office.target
        authorized = self.permit(target.app if target.app in {"PowerPoint", "Word"} else None)
        gesture = stabilizer.update(detections, now, authorized=authorized, context=target.key)
        active = [d for d in detections if d.gesture != Gesture.NEUTRAL]
        reason = (self.state.denial_reason(app=target.app) or "User authorization changed" if not authorized else
                  target.status if not target.ready else stabilizer.reason if not gesture else "")
        self.state.update_gesture(hand_detected=bool(detections), handedness=[d.hand for d in detections],
                                  detected=[str(d.gesture) for d in detections],
                                  confidence=[round(d.confidence, 3) for d in detections],
                                  stabilized=bool(gesture), authorized=authorized, timestamp=now,
                                  target=target.app, armed=stabilizer.armed, rejection_reason=reason)
        if active and reason:
            previous, logged = self._last_rejection
            if reason != previous or now - logged >= 2:
                log.info("gesture_rejected reason=%s gesture=%s target=%s", reason,
                         ",".join(str(d.gesture) for d in active), target.app)
                self._last_rejection = reason, now
        if gesture:
            d = stabilizer.last_detection
            snapshot = self.state.snapshot()
            command = self.settings.mappings.get(target.app, {}).get(str(gesture), "none")
            event = GestureEvent(str(gesture), d.hand, d.confidence, now, True, authorized,
                                 snapshot.auth_epoch, target, command,
                                 user_id=snapshot.identity_user["user_id"] if snapshot.identity_user else None)
            self.state.update_gesture(intended_command=command, last_gesture=str(gesture), command_dispatched=False)
            log.info("gesture_event name=%s hand=%s confidence=%.3f timestamp=%.3f stable=True authorized=%s target=%s command=%s epoch=%d",
                     gesture, d.hand, d.confidence, now, authorized, target.app, command, snapshot.auth_epoch)
            if target.ready:
                self.office.submit(event)
            else:
                self.state.update_gesture(rejection_reason=target.status, execution_result="Not dispatched")

    def run(self):
        hands = None
        try:
            if not self.settings.camera_consent:
                raise RuntimeError("Explicit camera consent is required before capture")
            # Keep OpenCV from oversubscribing every core alongside MediaPipe.
            cv2.setNumThreads(max(1, min(2, os.cpu_count() or 1)))
            samples = self.database.embeddings()
            self.enrolled = self.database.users.has_users() if self.multi_user else bool(len(samples))
            matcher = FaceMatcher(samples, self.settings.recognition_threshold, self.settings.recognition_margin)
            verify_models(model_dir())
            self.face_worker = FaceInferenceWorker(lambda: FaceEngine(model_dir()))
            self.face_worker.start()
            hands = HandTracker(model_dir() / "hand_landmarker.task")
            stabilizer = GestureStabilizer(self.settings.gesture_frames, self.settings.gesture_cooldown,
                                           self.settings.gesture_threshold)
            self.health_error = ""
            self.office.start()
            last_capture, last_submit, last_result, last_preview, last_error = 0., -1., 0, 0., 0.
            faces = []
            while not self.stop_event.is_set():
                tick = time.monotonic()
                matcher, stabilizer = self._commands(matcher, stabilizer)
                if tick - self.last_progress > 2:
                    self.challenge.reset()
                    self.directory.tracks.clear()
                    stabilizer.reset()
                    self.state.context(reason="Worker resumed after stall")
                if self.paused or self.session_is_locked:
                    if self.camera:
                        self.camera.stop()
                        self.camera = None
                    self.tick_policy(Observation(kind="paused"), tick,
                                     camera="Camera suspended", prompt="Monitoring paused" if self.paused else "Unlock using Windows")
                    self.last_progress = tick
                    self.stop_event.wait(.1)
                    continue
                if self.camera is None or self.camera.index != self.settings.camera_index:
                    if self.camera:
                        self.camera.stop()
                    self.camera = CameraManager(self.settings.camera_index)
                    self.camera.start()
                    self.challenge.reset()
                    self.directory.tracks.clear()
                frame, captured, error = self.camera.latest()
                if error or frame is None or tick - captured > 1:
                    self.challenge.reset()
                    self.tick_policy(Observation(camera_ok=False, kind="camera_error"), tick,
                                     camera=error or "Waiting for camera", prompt="Camera failure policy active")
                    self.last_progress = tick
                    self.stop_event.wait(.1)
                    continue
                if captured == last_capture:
                    self.last_progress = tick
                    self.stop_event.wait(.01)
                    continue
                last_capture = captured
                if tick - last_submit >= .20:
                    self.face_worker.submit(frame, captured)
                    last_submit = tick
                result = self.face_worker.latest()
                if result and result.sequence > last_result:
                    last_result = result.sequence
                    processed = self.process_faces(result, matcher)
                    if processed:
                        faces, _, matcher = processed
                current = self.state.snapshot()
                if tick - current.observed_at > .8 and tick - last_error >= .2:
                    self.tick_policy(Observation(camera_ok=False, kind="inference_delayed"), tick,
                                     camera="Face inference delayed", prompt="Waiting for fresh recognition result")
                    last_error = tick
                mirrored = cv2.flip(frame, 1)
                started = time.monotonic()
                try:
                    detections, landmarks = hands.detect(mirrored, int(captured * 1000), self.settings.swap_hands,
                                                          self.settings.pinch_ratio)
                except Exception as exc:
                    detections, landmarks = [], []
                    stabilizer.reset()
                    log.warning("hand_inference_failure type=%s", type(exc).__name__)
                    self.state.update_gesture(hand_error=f"Hand inference failed: {type(exc).__name__}")
                self.state.update_performance(hand_ms=round((time.monotonic() - started) * 1000, 1),
                                              capture_age_ms=round((tick - captured) * 1000, 1))
                self.dispatch_gesture(detections, stabilizer, time.monotonic())
                if tick - last_preview >= .1:
                    if self.settings.preview_visible or self.registration_preview:
                        self.render_preview(mirrored, faces, detections, landmarks)
                    last_preview = tick
                self.last_progress = time.monotonic()
                self.stop_event.wait(max(.005, .05 - (time.monotonic() - tick)))
        except Exception as exc:
            self.health_error = f"Inference stopped: {type(exc).__name__}; see the application log"
            self.state.context(reason=self.health_error)
            log.exception("Vision worker failed")
            self.event.emit(self.health_error)
        finally:
            self.state.context(reason="Worker stopped")
            cleanup = [self.office.stop]
            if self.camera:
                cleanup.append(self.camera.stop)
            if self.face_worker:
                cleanup.append(self.face_worker.stop)
            if hands:
                cleanup.append(hands.close)
            for close in cleanup:
                try:
                    close()
                except Exception:
                    log.exception("Worker cleanup failed")

    def render_preview(self, mirrored, faces, detections, landmarks):
        if self.settings.overlays:
            snapshot = self.state.snapshot()
            fresh = (time.monotonic() - snapshot.captured_at <= .8
                     and snapshot.face_count > 0 and snapshot.camera == "Connected")
            h, w = mirrored.shape[:2]
            # Face boxes are from unmirrored inference. Hand coordinates already
            # come from the mirrored frame; never mirror them or handedness twice.
            for face in faces if fresh else []:
                x, y, fw, fh = face.box
                identity = snapshot.identity_decision or snapshot.decision
                verified = face.matched and identity.controls_allowed and (snapshot.identity_user is None or face.user_id == snapshot.identity_user["user_id"])
                unknown = snapshot.enrolled and face.usable_for_verification and not face.matched and face.identity_status != "uncertain"
                if self.multi_user and snapshot.face_count > 1:
                    verified = face.matched and face.live
                color = (143, 230, 104) if verified else (100, 100, 250) if unknown else (90, 210, 255)
                display_name = face.user_name[:24] + ("..." if len(face.user_name) > 24 else "")
                label = (f"VERIFIED: {display_name}" if verified else "UNREGISTERED" if unknown else "VERIFYING") if self.multi_user else ("Verified" if verified else "Unknown" if unknown else "Verifying...")
                left, right = max(0, w - x - fw), min(w - 1, w - x)
                cv2.rectangle(mirrored, (left, y), (right, y + fh), color, 2, cv2.LINE_AA)
                self.preview_label(mirrored, label, left, max(24, y - 8), color)
            connections = ((0, 1), (1, 2), (2, 3), (3, 4), (0, 5), (5, 6), (6, 7), (7, 8),
                           (5, 9), (9, 10), (10, 11), (11, 12), (9, 13), (13, 14), (14, 15),
                           (15, 16), (13, 17), (0, 17), (17, 18), (18, 19), (19, 20))
            for points in landmarks:
                for start, end in connections:
                    if max(start, end) < len(points):
                        cv2.line(mirrored, points[start], points[end], (200, 192, 70), 2, cv2.LINE_AA)
                for px, py in points:
                    cv2.circle(mirrored, (px, py), 3, (240, 242, 200), -1, cv2.LINE_AA)
            for detection, points in zip(detections, landmarks):
                if not points:
                    continue
                name = str(detection.gesture).replace("_", " ")
                if detection.gesture in {Gesture.NEUTRAL, Gesture.AMBIGUOUS}:
                    name = "NO GESTURE"
                hand = detection.hand.upper()
                label = name if name in {"LEFT L", "RIGHT L"} else f"{hand} · {name}".replace("·", "|")
                px, py = points[0]
                self.preview_label(mirrored, label, px, max(24, min(h - 8, py - 18)), (230, 220, 105))
        rgb = cv2.cvtColor(mirrored, cv2.COLOR_BGR2RGB)
        h, w, _ = rgb.shape
        image = QImage(rgb.data, w, h, rgb.strides[0], QImage.Format.Format_RGB888).copy()
        with self._image_lock:
            self._latest_image = image

    @staticmethod
    def preview_label(frame, label, x, y, color):
        font, scale, thickness = cv2.FONT_HERSHEY_SIMPLEX, .52, 1
        (width, height), baseline = cv2.getTextSize(label, font, scale, thickness)
        x = max(4, min(x, frame.shape[1] - width - 12))
        y = max(height + 8, min(y, frame.shape[0] - baseline - 6))
        cv2.rectangle(frame, (x - 4, y - height - 6), (x + width + 5, y + baseline + 4), (18, 28, 35), -1)
        cv2.putText(frame, label, (x, y), font, scale, color, thickness, cv2.LINE_AA)

    def take_image(self):
        with self._image_lock:
            image, self._latest_image = self._latest_image, None
            return image

    def view(self):
        with self.state.lock:
            return self._view()

    def _view(self):
        snapshot = self.state.snapshot()
        identity = snapshot.identity_decision or snapshot.decision
        allowed = self.state.permit(app=snapshot.office.get("target"))
        office, gesture = snapshot.office, snapshot.gesture
        controls = ("Gestures disabled" if not snapshot.gestures_enabled else
                    "Additional face; gestures suspended" if snapshot.face_count > 1 else
                    "Suspended until face verification" if not allowed else
                    office.get("target_status", "Office discovery starting") if not office.get("target_ready") else
                    gesture.get("rejection_reason") or "Ready")
        return {"worker_id": self.worker_id, "revision": snapshot.revision,
                "face_status": snapshot.face_status, "face_count": snapshot.face_count,
                "enrolling": snapshot.enrolling, "gestures_enabled": snapshot.gestures_enabled,
                "target_ready": office.get("target_ready", False), "gesture_details": gesture,
                "state": str(identity.state), "remaining": snapshot.decision.remaining,
                "multi_user": self.multi_user, "user": snapshot.identity_user,
                "identity_in_grace": identity.in_grace, "gesture_access": allowed,
                "presence_authorized": snapshot.decision.controls_allowed,
                "workstation_state": str(snapshot.decision.state),
                "registration_samples": len(self.enrolling.samples) if self.enrolling else 0,
                "registration_poses": len(self.enrolling.poses) if self.enrolling else 0,
                "camera": snapshot.camera, "prompt": snapshot.prompt, "enrolled": self.enrolled,
                "presence": "Owner verified (brief grace)" if snapshot.decision.in_grace else
                            "Owner verified" if snapshot.decision.controls_allowed else "Owner unverified",
                "controls": controls, "target": office.get("target", "Discovering"),
                "office": office.get("target_status", "Office discovery starting") +
                          (f" · Last command: {office['command_result']}" if office.get("command_result") else ""),
                "gesture": ", ".join(f"{h} {g}" for h, g in zip(gesture.get("handedness", []), gesture.get("detected", []))) or "No hand",
                "last_gesture": gesture.get("last_gesture", "none"),
                "background": "Capture, face, hand and Office workers active",
                "diagnostics": self.state.diagnostics()}

    def stop(self):
        self.stop_event.set()
        self.invalidate("Stopping monitoring")
        return self.wait(8000)
