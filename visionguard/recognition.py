"""Multi-user matching and short-lived spatial tracks, using existing SFace vectors."""
from dataclasses import asdict
import numpy as np
from .authentication import unit, LivenessChallenge


def public_user(user):
    return {"user_id": user.user_id, "user_name": user.user_name, "role": user.role,
            "is_active": user.is_active, "is_owner": user.is_owner, "permissions": asdict(user.permissions)}


class RecognitionDirectory:
    def __init__(self, store, settings):
        self.store, self.settings = store, settings
        self.revision = -1
        self.users = {}
        self.tracks = {}
        self.next_track = 1
        self.refresh()

    def refresh(self):
        with self.store.lock:
            self.users = self.store.templates()
            self.revision = self.store.revision
        self.tracks.clear()

    def identify(self, embedding, retained_id=None):
        query = unit(embedding)
        candidates = []
        for user_id, (user, samples) in self.users.items():
            score = float(np.sort(samples @ query)[-2:].mean())
            cutoff = self.settings.recognition_threshold + self.settings.recognition_margin
            if user_id == retained_id:
                cutoff -= self.settings.recognition_hysteresis
            accepted = score >= cutoff
            candidates.append((score, accepted, user))
        candidates.sort(key=lambda c: c[0], reverse=True)
        if not candidates:
            return None, 0., "unregistered"
        score, accepted, user = candidates[0]
        # Never choose a name when two registered identities are too close.
        ambiguous = len(candidates) > 1 and score - candidates[1][0] < .05
        if accepted and not ambiguous:
            return user, score, "matched"
        return None, score, "uncertain" if ambiguous or score >= self.settings.recognition_threshold else "unregistered"

    @staticmethod
    def _overlap(a, b):
        ax, ay, aw, ah = a
        bx, by, bw, bh = b
        intersection = max(0, min(ax + aw, bx + bw) - max(ax, bx)) * max(0, min(ay + ah, by + bh) - max(ay, by))
        return intersection / max(1, aw * ah + bw * bh - intersection)

    def observe(self, faces, now, *, retained_id=None):
        track_ttl = max(.8, self.settings.verification_grace)
        self.tracks = {key: value for key, value in self.tracks.items() if now - value["seen"] <= track_ttl}
        unused = set(self.tracks)
        for face in faces:
            choices = [(self._overlap(face.box, self.tracks[key]["box"]), key) for key in unused]
            overlap, key = max(choices, default=(0, None))
            if overlap < .2:
                key = self.next_track
                self.next_track += 1
                self.tracks[key] = {"user_id": None, "challenge": LivenessChallenge(self.settings.verification_grace)}
            else:
                unused.remove(key)
            track = self.tracks[key]
            user, score, kind = self.identify(face.embedding, retained_id)
            face.score = score
            if not face.usable_for_verification:
                kind, user = "quality", None
            identity_id = user.user_id if user else None
            # Retain a challenge only through benign quality dropout on the same
            # spatial track. A different/ambiguous identity always invalidates it.
            if identity_id != track["user_id"] and kind != "quality":
                track["challenge"].reset()
                track["user_id"] = identity_id
            challenge = track["challenge"]
            challenge.grace = self.settings.verification_grace
            live = False
            if len(faces) == 1:
                live = challenge.update(-face.yaw if user else None, now,
                                        identity_ok=kind in {"quality", "matched"})
            elif user and challenge.valid_until > now:
                # Presence may use an existing lease; gestures still require a
                # single visible person. No multi-person challenge can advance.
                live = True
            else:
                challenge.reset()
            track.update(box=face.box, seen=now)
            face.track_id = key
            face.user_id = identity_id
            face.user_name = user.user_name if user else ""
            face.identity_status = kind
            face.matched = bool(user)
            face.live = bool(live and user)
            face.verification_prompt = challenge.prompt if user and not live else face.reason if kind == "quality" else ""
        return faces
