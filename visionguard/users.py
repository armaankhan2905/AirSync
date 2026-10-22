"""Persistent local identities, encrypted templates and administrator capabilities."""
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
import json
import secrets
import time
import uuid
import numpy as np
from .storage import StorageError

MODEL = "sface-2021dec"


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class RegistrationError(ValueError):
    pass


@dataclass(frozen=True)
class Permissions:
    can_control_powerpoint: bool = True
    can_control_word: bool = True
    can_use_gestures: bool = True
    can_manage_users: bool = False
    trusted_presence: bool = False

    def allows(self, app):
        return self.can_use_gestures and {"PowerPoint": self.can_control_powerpoint, "Word": self.can_control_word}.get(app, False)


@dataclass(frozen=True)
class User:
    user_id: str
    user_name: str
    role: str
    is_active: bool
    is_owner: bool
    permissions: Permissions
    created_at: str
    updated_at: str
    last_recognized_at: str | None = None


class UserStore:
    def __init__(self, database):
        self.database = database
        self.db, self.lock = database.db, database.lock
        self._sessions = {}
        with self.lock, self.db:
            self.db.execute("PRAGMA foreign_keys=ON")
            self.db.executescript("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id TEXT PRIMARY KEY, user_name TEXT NOT NULL,
                    role TEXT NOT NULL CHECK(role IN ('administrator','user')),
                    is_active INTEGER NOT NULL CHECK(is_active IN (0,1)),
                    is_owner INTEGER NOT NULL CHECK(is_owner IN (0,1)),
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    last_recognized_at TEXT, protected_policy BLOB NOT NULL);
                CREATE UNIQUE INDEX IF NOT EXISTS one_device_owner ON users(is_owner) WHERE is_owner=1;
                CREATE TABLE IF NOT EXISTS user_permissions (
                    user_id TEXT PRIMARY KEY REFERENCES users(user_id) ON DELETE CASCADE,
                    can_control_powerpoint INTEGER NOT NULL CHECK(can_control_powerpoint IN (0,1)),
                    can_control_word INTEGER NOT NULL CHECK(can_control_word IN (0,1)),
                    can_use_gestures INTEGER NOT NULL CHECK(can_use_gestures IN (0,1)),
                    can_manage_users INTEGER NOT NULL CHECK(can_manage_users IN (0,1)),
                    trusted_presence INTEGER NOT NULL CHECK(trusted_presence IN (0,1)));
                CREATE TABLE IF NOT EXISTS face_templates (
                    template_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                    encrypted_embedding BLOB NOT NULL, model_version TEXT NOT NULL, created_at TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS templates_by_user ON face_templates(user_id);
                CREATE TABLE IF NOT EXISTS recognition_events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT REFERENCES users(user_id) ON DELETE SET NULL,
                    event_type TEXT NOT NULL, timestamp TEXT NOT NULL, result TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS events_by_user_time ON recognition_events(user_id,timestamp);
                CREATE TABLE IF NOT EXISTS directory_state (id INTEGER PRIMARY KEY CHECK(id=1), revision INTEGER NOT NULL);
                INSERT OR IGNORE INTO directory_state VALUES(1,0);
            """)

    @property
    def revision(self):
        with self.lock:
            return self.db.execute("SELECT revision FROM directory_state WHERE id=1").fetchone()[0]

    def _changed(self):
        self.db.execute("UPDATE directory_state SET revision=revision+1 WHERE id=1")

    def has_users(self):
        with self.lock:
            return bool(self.db.execute("SELECT 1 FROM users LIMIT 1").fetchone())

    def authenticate_admin(self, passphrase, *, actor_id=None):
        # The local administrative recovery credential also permits management
        # when no camera identity is available. Named delegates need permission.
        if not self.database.authorize(passphrase):
            raise PermissionError("Administrator authentication failed or temporarily rate limited")
        if actor_id is not None:
            actor = self.get(actor_id)
            if not actor or not actor.is_active or not actor.permissions.can_manage_users:
                raise PermissionError("User management permission required")
        token = secrets.token_urlsafe(32)
        with self.lock:
            self._sessions = {key: value for key, value in self._sessions.items() if value[0] > time.monotonic()}
            self._sessions[token] = (time.monotonic() + 300, actor_id)
        return token

    def require_admin(self, token):
        with self.lock:
            session = self._sessions.get(token) if isinstance(token, str) else None
            if not session or time.monotonic() >= session[0]:
                raise PermissionError("Administrator authorization expired; authenticate again")
            if session[1] is not None:
                actor = self.get(session[1])
                if not actor or not actor.is_active or not actor.permissions.can_manage_users:
                    raise PermissionError("User management permission revoked")

    def revoke_sessions(self):
        with self.lock:
            self._sessions.clear()

    @staticmethod
    def _samples(samples):
        try:
            a = np.asarray(samples, dtype=np.float32)
        except (ValueError, TypeError) as exc:
            raise RegistrationError("At least six finite SFace samples required") from exc
        if a.ndim != 2 or a.shape[1] != 128 or len(a) < 6 or not np.isfinite(a).all():
            raise RegistrationError("At least six finite SFace samples required")
        norms = np.linalg.norm(a, axis=1, keepdims=True)
        if np.any(norms < 1e-6):
            raise RegistrationError("Zero embeddings cannot be registered")
        return a / norms

    @staticmethod
    def _name(name):
        if not isinstance(name, str):
            raise RegistrationError("Enter a user name of 1–60 characters")
        name = name.strip()
        if not 1 <= len(name) <= 60 or any(ord(c) < 32 for c in name):
            raise RegistrationError("Enter a user name of 1–60 characters")
        return name

    def _seal_policy(self, user_id, name, role, active, owner, permissions):
        return self.database.protector.protect(json.dumps({"user_id": user_id, "user_name": name, "role": role,
            "is_active": bool(active), "is_owner": bool(owner), "permissions": asdict(permissions)}, allow_nan=False).encode())

    def get(self, user_id):
        with self.lock:
            row = self.db.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()
            permissions = self.db.execute("SELECT * FROM user_permissions WHERE user_id=?", (user_id,)).fetchone()
        if row is None:
            return None
        try:
            if permissions is None:
                raise ValueError("Missing permissions")
            policy = json.loads(self.database.protector.unprotect(row[8]))
            p = Permissions(*(bool(x) for x in permissions[1:]))
            expected = {"user_id": row[0], "user_name": row[1], "role": row[2], "is_active": bool(row[3]),
                        "is_owner": bool(row[4]), "permissions": asdict(p)}
            if policy != expected:
                raise ValueError("Protected permission metadata mismatch")
            return User(row[0], row[1], row[2], bool(row[3]), bool(row[4]), p, row[5], row[6], row[7])
        except Exception as exc:
            raise StorageError("User authorization metadata is corrupted; access suspended") from exc

    def list_users(self):
        with self.lock:
            ids = [r[0] for r in self.db.execute("SELECT user_id FROM users ORDER BY created_at,user_id")]
            return [self.get(user_id) for user_id in ids]

    def templates(self, *, include_disabled=False):
        result = {}
        with self.lock:
            for user in self.list_users():
                # Validate every template, including disabled accounts, so damage
                # cannot silently remove a duplicate or become an empty profile.
                rows = self.db.execute("SELECT encrypted_embedding,model_version FROM face_templates WHERE user_id=?",
                                       (user.user_id,)).fetchall()
                try:
                    if len(rows) < 6:
                        raise ValueError("Insufficient templates")
                    samples = []
                    for blob, model in rows:
                        if model != MODEL:
                            raise ValueError("Incompatible embedding model")
                        template = json.loads(self.database.protector.unprotect(blob))
                        if template["user_id"] != user.user_id or template["model_version"] != model:
                            raise ValueError("Template identity binding mismatch")
                        a = np.asarray(template["embedding"], dtype=np.float32)
                        if a.shape != (128,) or not np.isfinite(a).all() or np.linalg.norm(a) < 1e-6:
                            raise ValueError("Invalid face template")
                        samples.append(a)
                    samples = self._samples(samples)
                except Exception as exc:
                    raise StorageError("Registered face templates are corrupted; monitoring suspended") from exc
                if user.is_active or include_disabled:
                    result[user.user_id] = (user, samples)
        return result

    def _write_templates(self, user_id, samples):
        self.db.execute("DELETE FROM face_templates WHERE user_id=?", (user_id,))
        self.db.executemany("INSERT INTO face_templates VALUES(?,?,?,?,?)", [
            (uuid.uuid4().hex, user_id, self.database.protector.protect(json.dumps({"user_id": user_id,
                "model_version": MODEL, "embedding": a.tolist()}).encode()), MODEL, utc_now())
            for a in samples])

    def _duplicate(self, samples, exclude=None):
        for user_id, (_, existing) in self.templates(include_disabled=True).items():
            if user_id == exclude:
                continue
            scores = np.sort(samples @ existing.T, axis=1)[:, -2:].mean(axis=1)
            if float(np.max(scores)) >= .53:
                raise RegistrationError("User already registered.")

    def register(self, name, samples, token, *, role="user", permissions=None):
        name, samples = self._name(name), self._samples(samples)
        if role not in {"administrator", "user"}:
            raise RegistrationError("Invalid user role")
        with self.lock, self.db:
            self.require_admin(token)
            self._duplicate(samples)
            first = not self.has_users()
            if first:
                role = "administrator"
            p = permissions or Permissions(can_manage_users=role == "administrator", trusted_presence=first)
            if first:
                p = Permissions(True, True, True, True, True)
            self._validate_permissions(p, role, first)
            user_id, now = uuid.uuid4().hex, utc_now()
            self.db.execute("INSERT INTO users VALUES(?,?,?,?,?,?,?,?,?)", (user_id, name, role, 1, int(first), now, now, None,
                self._seal_policy(user_id, name, role, True, first, p)))
            self.db.execute("INSERT INTO user_permissions VALUES(?,?,?,?,?,?)", (user_id, *map(int, asdict(p).values())))
            self._write_templates(user_id, samples)
            self._changed()
            self._event(user_id, "registered", "success")
        return self.get(user_id)

    @staticmethod
    def _validate_permissions(p, role, owner):
        if not isinstance(p, Permissions) or any(type(v) is not bool for v in asdict(p).values()):
            raise RegistrationError("Invalid application permissions")
        if role == "administrator" and not p.can_manage_users:
            raise RegistrationError("Administrators must retain user-management permission")
        if owner and not p.trusted_presence:
            raise RegistrationError("The device owner must retain workstation-presence trust")

    def update(self, user_id, token, *, name=None, role=None, active=None, permissions=None):
        with self.lock, self.db:
            self.require_admin(token)
            user = self.get(user_id)
            if not user:
                raise RegistrationError("User no longer exists")
            role = role or user.role
            if role not in {"administrator", "user"} or (active is not None and type(active) is not bool):
                raise RegistrationError("Invalid role or active state")
            active = user.is_active if active is None else active
            name = self._name(name if name is not None else user.user_name)
            p = permissions if permissions is not None else (
                replace(user.permissions, can_manage_users=role == "administrator") if role != user.role else user.permissions)
            self._validate_permissions(p, role, user.is_owner)
            if user.is_owner and not active:
                raise RegistrationError("Transfer device ownership before disabling this user")
            if user.role == "administrator" and user.is_active and (not active or role != "administrator"):
                self._require_other_admin(user_id)
            self.db.execute("UPDATE users SET user_name=?,role=?,is_active=?,updated_at=?,protected_policy=? WHERE user_id=?",
                            (name, role, int(active), utc_now(), self._seal_policy(user_id, name, role, active, user.is_owner, p), user_id))
            self.db.execute("UPDATE user_permissions SET can_control_powerpoint=?,can_control_word=?,can_use_gestures=?,can_manage_users=?,trusted_presence=? WHERE user_id=?",
                            (*map(int, asdict(p).values()), user_id))
            self._changed()
            self._event(user_id, "permissions_changed", "success")
        return self.get(user_id)

    def _require_other_admin(self, user_id):
        if not any(u.user_id != user_id and u.is_active and u.role == "administrator" for u in self.list_users()):
            raise RegistrationError("Keep at least one active administrator")

    def set_owner(self, user_id, token):
        with self.lock, self.db:
            self.require_admin(token)
            new_owner = self.get(user_id)
            if not new_owner or not new_owner.is_active:
                raise RegistrationError("Select an active registered user")
            # Clear the unique owner flag first, then reseal both protected policies.
            previous = [u for u in self.list_users() if u.is_owner and u.user_id != user_id]
            for user in previous:
                self.db.execute("UPDATE users SET is_owner=0,updated_at=?,protected_policy=? WHERE user_id=?",
                    (utc_now(), self._seal_policy(user.user_id, user.user_name, user.role, user.is_active, False, user.permissions), user.user_id))
            p = Permissions(**{**asdict(new_owner.permissions), "trusted_presence": True})
            self.db.execute("UPDATE users SET is_owner=1,updated_at=?,protected_policy=? WHERE user_id=?",
                (utc_now(), self._seal_policy(user_id, new_owner.user_name, new_owner.role, True, True, p), user_id))
            self.db.execute("UPDATE user_permissions SET trusted_presence=1 WHERE user_id=?", (user_id,))
            self._changed()

    def reenroll(self, user_id, samples, token):
        samples = self._samples(samples)
        with self.lock, self.db:
            self.require_admin(token)
            if self.get(user_id) is None:
                raise RegistrationError("User no longer exists")
            self._duplicate(samples, exclude=user_id)
            self._write_templates(user_id, samples)
            self.db.execute("UPDATE users SET updated_at=? WHERE user_id=?", (utc_now(), user_id))
            self._changed()
            self._event(user_id, "reenrolled", "success")

    def delete(self, user_id, token):
        with self.lock, self.db:
            self.require_admin(token)
            user = self.get(user_id)
            if not user:
                raise RegistrationError("User no longer exists")
            if user.is_owner:
                raise RegistrationError("Transfer device ownership before deleting this user")
            if user.role == "administrator" and user.is_active:
                self._require_other_admin(user_id)
            self.db.execute("DELETE FROM users WHERE user_id=?", (user_id,))
            self._changed()
            self._event(None, "user_deleted", "success")
        with self.lock:
            self.db.execute("VACUUM")

    def _event(self, user_id, event_type, result):
        self.db.execute("INSERT INTO recognition_events(user_id,event_type,timestamp,result) VALUES(?,?,?,?)",
                        (user_id, event_type, utc_now(), result))
        self.db.execute("DELETE FROM recognition_events WHERE event_id NOT IN (SELECT event_id FROM recognition_events ORDER BY event_id DESC LIMIT 2000)")

    def record_recognition(self, user_id, event_type, result):
        with self.lock, self.db:
            if user_id and self.get(user_id) is None:
                user_id = None
            self._event(user_id, event_type, result)
            if user_id:
                self.db.execute("UPDATE users SET last_recognized_at=? WHERE user_id=?", (utc_now(), user_id))

    def migrate_legacy(self):
        # One atomic transaction: preserve the existing owner and credential;
        # never silently replace a damaged profile with an empty directory.
        with self.lock:
            legacy = self.database.get("enrollment")
            if legacy is None:
                return False
            samples = self.database.embeddings()
            if self.has_users():
                raise StorageError("Legacy and multi-user profiles conflict; restore a consistent backup")
            if self.database.get("authorization") is None:
                raise StorageError("Existing owner is missing its administrative recovery credential")
            samples = self._samples(samples)
            user_id, now = uuid.uuid4().hex, utc_now()
            p = Permissions(True, True, True, True, True)
            with self.db:
                self.db.execute("INSERT INTO users VALUES(?,?,?,?,?,?,?,?,?)",
                    (user_id, "Device Owner", "administrator", 1, 1, now, now, None,
                     self._seal_policy(user_id, "Device Owner", "administrator", True, True, p)))
                self.db.execute("INSERT INTO user_permissions VALUES(?,?,?,?,?,?)", (user_id, 1, 1, 1, 1, 1))
                self._write_templates(user_id, samples)
                self._changed()
                self._event(user_id, "legacy_owner_migrated", "success")
                self.db.execute("DELETE FROM secrets WHERE key='enrollment'")
            return True
