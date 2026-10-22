"""SQLite persistence with user-scoped Windows DPAPI. No plaintext fallback."""
import hashlib
import hmac
import json
from pathlib import Path
import secrets
import sqlite3
import threading
import time
from dataclasses import asdict
import numpy as np
from .config import Settings


class DPAPI:
    def protect(self, data: bytes) -> bytes:
        import win32crypt
        return win32crypt.CryptProtectData(data, "VisionGuard AI", None, None, None, 1)

    def unprotect(self, data: bytes) -> bytes:
        import win32crypt
        return win32crypt.CryptUnprotectData(data, None, None, None, 1)[1]


class StorageError(RuntimeError):
    pass


class Database:
    def __init__(self, path: Path, protector=None):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.execute("PRAGMA secure_delete=ON")
        self.db.execute("CREATE TABLE IF NOT EXISTS secrets (key TEXT PRIMARY KEY, value BLOB NOT NULL)")
        self.db.commit()
        self.protector = protector or DPAPI()
        self.lock = threading.RLock()
        self.failed_attempts = 0
        self.retry_after = 0.0
        from .users import UserStore
        self.users = UserStore(self)

    def get(self, key: str):
        with self.lock:
            row = self.db.execute("SELECT value FROM secrets WHERE key=?", (key,)).fetchone()
        if row is None:
            return None
        try:
            return json.loads(self.protector.unprotect(row[0]))
        except Exception as exc:
            raise StorageError("Protected local data cannot be read; restore your Windows profile backup.") from exc

    def put(self, key: str, value):
        encrypted = self.protector.protect(json.dumps(value, allow_nan=False).encode())
        with self.lock, self.db:
            self.db.execute("INSERT OR REPLACE INTO secrets VALUES (?,?)", (key, encrypted))

    def settings(self) -> Settings:
        try:
            return Settings(**(self.get("settings") or {})).validate()
        except (ValueError, TypeError) as exc:
            raise StorageError("Stored configuration is invalid") from exc

    def save_settings(self, settings: Settings):
        self.put("settings", asdict(settings.validate()))

    def embeddings(self) -> np.ndarray:
        value = self.get("enrollment")
        if value is None:
            return np.empty((0, 128), dtype=np.float32)
        try:
            if value["model"] != "sface-2021dec":
                raise ValueError("Incompatible embedding model")
            a = np.asarray(value["embeddings"], dtype=np.float32)
            if a.ndim != 2 or a.shape[1] != 128 or len(a) < 6 or not np.isfinite(a).all():
                raise ValueError("Invalid embeddings")
            if np.any(np.linalg.norm(a, axis=1) < 1e-6):
                raise ValueError("Zero embeddings")
            return a
        except (KeyError, ValueError, TypeError) as exc:
            raise StorageError("Enrollment is corrupted; monitoring is suspended") from exc

    def enroll(self, embeddings: np.ndarray):
        a = np.asarray(embeddings, dtype=np.float32)
        if a.ndim != 2 or a.shape[1] != 128 or len(a) < 6 or not np.isfinite(a).all():
            raise ValueError("At least six finite SFace samples required")
        if np.any(np.linalg.norm(a, axis=1) < 1e-6):
            raise ValueError("Zero embeddings cannot be enrolled")
        self.put("enrollment", {"model": "sface-2021dec", "embeddings": a.tolist()})

    def delete_enrollment(self):
        with self.lock, self.db:
            self.db.execute("DELETE FROM secrets WHERE key='enrollment'")
        with self.lock:
            self.db.execute("VACUUM")

    def set_passphrase(self, passphrase: str):
        if len(passphrase) < 12:
            raise ValueError("Use at least 12 characters for the application recovery passphrase")
        salt = secrets.token_bytes(16)
        digest = hashlib.scrypt(passphrase.encode(), salt=salt, n=16384, r=8, p=1)
        self.put("authorization", {"salt": salt.hex(), "digest": digest.hex()})
        self.users.revoke_sessions()

    def authorize(self, passphrase: str, now=None) -> bool:
        with self.lock:
            now = time.monotonic() if now is None else now
            if now < self.retry_after:
                return False
            record = self.authorization_record()
            if record is None:
                return False
            digest = hashlib.scrypt(passphrase.encode(), salt=bytes.fromhex(record["salt"]), n=16384, r=8, p=1)
            valid = hmac.compare_digest(digest.hex(), record["digest"])
            self.failed_attempts = 0 if valid else self.failed_attempts + 1
            self.retry_after = now + min(60, 2 ** self.failed_attempts) if not valid else 0
            return valid

    def authorization_record(self):
        record = self.get("authorization")
        if record is None:
            return None
        try:
            if len(bytes.fromhex(record["salt"])) != 16 or len(bytes.fromhex(record["digest"])) != 64:
                raise ValueError("Invalid administrative credential")
        except (KeyError, TypeError, ValueError) as exc:
            raise StorageError("Administrative authorization record is corrupted; restore your profile backup") from exc
        return record

    def close(self):
        with self.lock:
            self.db.close()
