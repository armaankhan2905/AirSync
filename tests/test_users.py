"""Persistent encrypted registration, administrator capabilities and recovery."""
from dataclasses import replace
import numpy as np
import pytest
from visionguard.users import Permissions, RegistrationError
from visionguard.storage import Database, StorageError

PASSPHRASE = "local-administrator-test-passphrase"


@pytest.fixture
def admin_token(database):
    database.set_passphrase(PASSPHRASE)
    return database.users.authenticate_admin(PASSPHRASE)


def samples(index):
    return np.tile(np.eye(128, dtype=np.float32)[index], (6, 1))


def test_first_registration_is_administrator_and_owner(database, admin_token):
    user = database.users.register("Armaan", samples(0), admin_token)
    assert user.user_name == "Armaan" and user.role == "administrator"
    assert user.is_owner and user.permissions.trusted_presence and user.permissions.can_manage_users
    assert user.permissions.allows("PowerPoint") and user.permissions.allows("Word")
    assert len(database.users.templates()[user.user_id][1]) == 6


def test_registration_requires_real_unexpired_administrator_capability(database, monkeypatch):
    with pytest.raises(PermissionError):
        database.users.register("Intruder", samples(0), "forged-token")
    assert not database.users.has_users()
    database.set_passphrase(PASSPHRASE)
    with pytest.raises(PermissionError):
        database.users.authenticate_admin("wrong")


def test_capability_expiry_and_password_change_revoke_registration(database, admin_token, monkeypatch):
    database.users._sessions[admin_token] = (0., None)
    with pytest.raises(PermissionError):
        database.users.register("Expired", samples(0), admin_token)
    token = database.users.authenticate_admin(PASSPHRASE)
    database.set_passphrase("replacement-administrator-passphrase")
    with pytest.raises(PermissionError):
        database.users.register("Revoked", samples(0), token)


def test_duplicates_including_disabled_accounts_are_rejected(database, admin_token):
    database.users.register("Owner", samples(0), admin_token)
    guest = database.users.register("Presenter", samples(1), admin_token)
    database.users.update(guest.user_id, admin_token, active=False)
    with pytest.raises(RegistrationError, match="User already registered"):
        database.users.register("Other name", samples(1), admin_token)
    assert len(database.users.list_users()) == 2
    assert guest.user_id not in database.users.templates()


def test_registered_users_survive_database_reopen(database, admin_token):
    user = database.users.register("Persistent", samples(0), admin_token)
    path = database.db.execute("PRAGMA database_list").fetchone()[2]
    reopened = Database(__import__('pathlib').Path(path), database.protector)
    try:
        assert reopened.users.get(user.user_id).user_name == "Persistent"
        assert np.allclose(reopened.users.templates()[user.user_id][1], samples(0))
        with pytest.raises(PermissionError):
            reopened.users.register("No persisted login", samples(1), admin_token)
    finally:
        reopened.close()


def test_biometric_templates_and_permissions_are_protected(database, admin_token):
    user = database.users.register("Owner", samples(0), admin_token)
    blobs = database.db.execute("SELECT encrypted_embedding FROM face_templates").fetchall()
    assert len(blobs) == 6
    assert all(b"[1.0," not in row[0] for row in blobs)
    policy = database.db.execute("SELECT protected_policy FROM users").fetchone()[0]
    assert b"can_manage_users" not in policy and b"Owner" not in policy
    database.db.execute("UPDATE user_permissions SET can_control_word=0 WHERE user_id=?", (user.user_id,))
    database.db.commit()
    with pytest.raises(StorageError, match="authorization metadata"):
        database.users.get(user.user_id)


@pytest.mark.parametrize("damage", [b"BAD", b"TEST:bad-json"])
def test_corrupt_templates_fail_closed(database, admin_token, damage):
    database.users.register("Owner", samples(0), admin_token)
    database.db.execute("UPDATE face_templates SET encrypted_embedding=?", (damage,))
    database.db.commit()
    with pytest.raises(StorageError, match="templates are corrupted"):
        database.users.templates()


@pytest.mark.parametrize("case", ["missing", "model", "shape", "nan", "zero"])
def test_invalid_template_records_never_become_empty_registration(database, admin_token, case):
    database.users.register("Owner", samples(0), admin_token)
    if case == "missing":
        database.db.execute("DELETE FROM face_templates WHERE template_id=(SELECT template_id FROM face_templates LIMIT 1)")
    elif case == "model":
        database.db.execute("UPDATE face_templates SET model_version='other-model'")
    else:
        import json
        value = [0.] * (12 if case == "shape" else 128)
        if case == "nan":
            value[0] = float('nan')
        blob = database.protector.protect(json.dumps(value).encode())
        database.db.execute("UPDATE face_templates SET encrypted_embedding=?", (blob,))
    database.db.commit()
    with pytest.raises(StorageError):
        database.users.templates()


def test_presenter_defaults_do_not_grant_workstation_trust(database, admin_token):
    database.users.register("Owner", samples(0), admin_token)
    guest = database.users.register("Presenter", samples(1), admin_token)
    assert guest.role == "user" and not guest.is_owner
    assert not guest.permissions.can_manage_users and not guest.permissions.trusted_presence
    assert guest.permissions.allows("PowerPoint")
    with pytest.raises(PermissionError, match="management permission"):
        database.users.authenticate_admin(PASSPHRASE, actor_id=guest.user_id)
    database.users.update(guest.user_id, admin_token,
        permissions=replace(guest.permissions, can_manage_users=True))
    delegate_token = database.users.authenticate_admin(PASSPHRASE, actor_id=guest.user_id)
    database.users.require_admin(delegate_token)
    database.users.update(guest.user_id, admin_token, active=False)
    with pytest.raises(PermissionError):
        database.users.require_admin(delegate_token)


def test_delete_cascades_templates_and_permissions_and_nulls_audit_identity(database, admin_token):
    database.users.register("Owner", samples(0), admin_token)
    guest = database.users.register("Presenter", samples(1), admin_token)
    database.users.record_recognition(guest.user_id, "verified", "success")
    revision = database.users.revision
    database.users.delete(guest.user_id, admin_token)
    assert database.users.get(guest.user_id) is None
    assert database.users.revision > revision
    for table in ("face_templates", "user_permissions"):
        assert database.db.execute(f"SELECT COUNT(*) FROM {table} WHERE user_id=?", (guest.user_id,)).fetchone()[0] == 0
    assert not database.db.execute("PRAGMA foreign_key_check").fetchall()


def test_owner_and_last_administrator_protected_but_ownership_can_transfer(database, admin_token):
    owner = database.users.register("Owner", samples(0), admin_token)
    with pytest.raises(RegistrationError, match="Transfer"):
        database.users.delete(owner.user_id, admin_token)
    with pytest.raises(RegistrationError, match="active administrator"):
        database.users.update(owner.user_id, admin_token, role="user")
    second = database.users.register("Other admin", samples(1), admin_token, role="administrator")
    database.users.set_owner(second.user_id, admin_token)
    assert database.users.get(second.user_id).is_owner
    assert not database.users.get(owner.user_id).is_owner
    database.users.delete(owner.user_id, admin_token)


def test_reenrollment_replaces_only_selected_users_templates(database, admin_token):
    owner = database.users.register("Owner", samples(0), admin_token)
    guest = database.users.register("Guest", samples(1), admin_token)
    with pytest.raises(RegistrationError, match="already registered"):
        database.users.reenroll(guest.user_id, samples(0), admin_token)
    database.users.reenroll(guest.user_id, samples(2), admin_token)
    catalog = database.users.templates()
    assert np.allclose(catalog[owner.user_id][1], samples(0))
    assert np.allclose(catalog[guest.user_id][1], samples(2))


def test_legacy_owner_migration_is_atomic_and_preserves_credential(database, admin_token, monkeypatch):
    database.enroll(samples(0))
    original = database.users._write_templates
    monkeypatch.setattr(database.users, "_write_templates", lambda *args: (_ for _ in ()).throw(RuntimeError("simulated disk failure")))
    with pytest.raises(RuntimeError):
        database.users.migrate_legacy()
    assert not database.users.has_users() and len(database.embeddings()) == 6
    monkeypatch.setattr(database.users, "_write_templates", original)
    assert database.users.migrate_legacy()
    assert not database.users.migrate_legacy()
    assert database.authorize(PASSPHRASE)
    assert database.get("enrollment") is None
    owner = database.users.list_users()[0]
    assert owner.is_owner and owner.role == "administrator" and owner.permissions.trusted_presence


def test_corrupt_legacy_enrollment_is_not_silently_migrated(database, admin_token):
    database.put("enrollment", {"model": "wrong-model", "embeddings": samples(0).tolist()})
    with pytest.raises(StorageError):
        database.users.migrate_legacy()
    assert not database.users.has_users()


def test_encrypted_templates_cannot_be_reassigned_to_another_user(database, admin_token):
    owner = database.users.register("Owner", samples(0), admin_token)
    guest = database.users.register("Guest", samples(1), admin_token)
    blob = database.db.execute("SELECT encrypted_embedding FROM face_templates WHERE user_id=? LIMIT 1", (owner.user_id,)).fetchone()[0]
    database.db.execute("UPDATE face_templates SET encrypted_embedding=? WHERE user_id=?", (blob, guest.user_id))
    database.db.commit()
    with pytest.raises(StorageError):
        database.users.templates()


@pytest.mark.skipif(__import__('sys').platform != 'win32', reason='Windows DPAPI')
def test_real_dpapi_template_registration_and_reopen(tmp_path):
    path = tmp_path / 'protected-users.sqlite3'
    db = Database(path)
    db.set_passphrase(PASSPHRASE)
    user = db.users.register("DPAPI test", samples(0), db.users.authenticate_admin(PASSPHRASE))
    blob = db.db.execute("SELECT encrypted_embedding FROM face_templates LIMIT 1").fetchone()[0]
    assert b'"embedding"' not in blob and b'"user_id"' not in blob
    db.close()
    reopened = Database(path)
    try:
        assert np.allclose(reopened.users.templates()[user.user_id][1], samples(0))
    finally:
        reopened.close()


def test_invalid_administrative_record_is_a_recovery_error(database):
    database.put('authorization', {'salt': 'not-hex', 'digest': 'bad'})
    with pytest.raises(StorageError, match='authorization record is corrupted'):
        database.users.authenticate_admin(PASSPHRASE)


def test_role_changes_apply_safe_management_defaults(database, admin_token):
    database.users.register("Owner", samples(0), admin_token)
    user = database.users.register("Presenter", samples(1), admin_token)
    promoted = database.users.update(user.user_id, admin_token, role="administrator")
    assert promoted.permissions.can_manage_users
    demoted = database.users.update(user.user_id, admin_token, role="user")
    assert not demoted.permissions.can_manage_users
    assert not demoted.permissions.trusted_presence
