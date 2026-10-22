import sqlite3
from unittest.mock import Mock
import numpy as np
import pytest
from visionguard.config import Settings
from visionguard.security import LockManager
from visionguard.storage import Database, DPAPI, StorageError


def test_settings_and_enrollment_roundtrip(database, embedding):
    settings = Settings(absence_timeout=8)
    database.save_settings(settings)
    assert database.settings().absence_timeout == 8
    database.enroll(np.tile(embedding, (6, 1)))
    assert database.embeddings().shape == (6, 128)
    database.delete_enrollment()
    assert len(database.embeddings()) == 0


def test_sqlite_does_not_contain_plaintext(database, embedding):
    database.enroll(np.tile(embedding, (6, 1)))
    blob = database.db.execute("SELECT value FROM secrets WHERE key='enrollment'").fetchone()[0]
    assert b"embeddings" not in blob and b"sface" not in blob


def test_corrupt_enrollment_fails_closed(database):
    database.put("enrollment", {"embeddings": [[0] * 128] * 6})
    with pytest.raises(StorageError):
        database.embeddings()
    database.db.execute("UPDATE secrets SET value=? WHERE key='enrollment'", (b"BAD",))
    database.db.commit()
    with pytest.raises(StorageError):
        database.embeddings()


def test_recovery_authorization_and_throttling(database):
    with pytest.raises(ValueError):
        database.set_passphrase("short")
    database.set_passphrase("application-only-long-passphrase")
    assert not database.authorize("wrong", now=0)
    assert not database.authorize("application-only-long-passphrase", now=1)
    assert database.authorize("application-only-long-passphrase", now=10)
    assert b"application-only" not in database.db.execute("SELECT value FROM secrets WHERE key='authorization'").fetchone()[0]


@pytest.mark.parametrize("changes", [
    {"absence_timeout": -1}, {"recognition_threshold": .2}, {"gesture_cooldown": 0},
    {"camera_policy": "ignore"}, {"camera_index": 1.2}, {"auto_lock": "yes"},
    {"absence_timeout": float("nan")}, {"mappings": {}},
])
def test_invalid_settings_rejected(changes):
    with pytest.raises(ValueError):
        Settings(**changes).validate()


def test_simulation_never_calls_windows_API():
    api = Mock(return_value=True)
    assert LockManager(simulation=True, lock_api=api).lock()
    api.assert_not_called()


def test_real_lock_adapter_only_uses_injected_mock():
    api = Mock(return_value=True)
    assert LockManager(simulation=False, lock_api=api).lock()
    api.assert_called_once_with()


def test_lock_api_failure_is_reported_without_calling_windows():
    with pytest.raises(RuntimeError, match="rejected"):
        LockManager(simulation=False, lock_api=Mock(return_value=False)).lock()


@pytest.mark.skipif(__import__('sys').platform != "win32", reason="Windows DPAPI integration")
def test_windows_dpapi_actual_roundtrip(tmp_path):
    db = Database(tmp_path / "dpapi.sqlite3")
    try:
        db.put("test", {"value": "private-test-value"})
        assert db.get("test") == {"value": "private-test-value"}
        blob = db.db.execute("SELECT value FROM secrets").fetchone()[0]
        assert b"private-test-value" not in blob
    finally:
        db.close()
