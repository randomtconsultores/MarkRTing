import hashlib

import pytest

from mini_marketo import passwords
from mini_marketo.repositories.access_repository import AccessRepository
from mini_marketo.services.admin_account_service import (
    AccountConflict, AdminAccountService, PasswordPolicy, validate_username,
)


GOOD = " uma frase longa de teste 🔐 "
BLOCKED = "password-comprometida"


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


@pytest.fixture
def policy():
    return PasswordPolicy([digest(BLOCKED)])


@pytest.fixture
def repo(db):
    if db.kind == "sqlite":
        return AccessRepository(db.conn)
    from mini_marketo.repositories.postgres_access_repository import PostgresAccessRepository
    return PostgresAccessRepository(db.conn)


@pytest.fixture
def service(repo, policy):
    return AdminAccountService(repo, policy)


@pytest.mark.parametrize("value", [None, 1, b"operator", "", "Operator", " operator", "operator ", "x" * 129, "bad\x00name", "\ud800"])
def test_invalid_username(value):
    with pytest.raises(ValueError):
        validate_username(value)


@pytest.mark.parametrize("value", ["operator", "a" * 128, "joão"])
def test_valid_username(value):
    assert validate_username(value) == value


@pytest.mark.parametrize("blocked", [None, [], "a" * 64, [None], ["g" * 64], ["A" * 64], ["a" * 63], ["a" * 65]])
def test_blocklist_mandatory_and_validated(blocked):
    with pytest.raises(ValueError):
        PasswordPolicy(blocked)


@pytest.mark.parametrize("password", [None, 1, b"password", "", "x" * 14, "é" * 2049, "x" * 4097, "\ud800" * 15, BLOCKED])
def test_password_policy_rejects_invalid_or_blocked(policy, password):
    with pytest.raises(ValueError) as error:
        policy.validate(password, "operator")
    if isinstance(password, str) and len(password) >= 15:
        assert password not in str(error.value)


@pytest.mark.parametrize("password", ["x" * 15, "x" * 64, "é" * 2048, GOOD, "um\x00segredo longo suficiente"])
def test_password_policy_preserves_allowed_text(policy, password):
    assert policy.validate(password, "operator") is None


def test_password_cannot_equal_username(policy):
    with pytest.raises(ValueError):
        policy.validate("operator-account-name", "operator-account-name")


@pytest.mark.parametrize("role", ["admin", "viewer"])
def test_create_account_with_real_argon2(service, repo, role):
    user = service.create("operator", GOOD, role, now=100)
    assert user.is_active and user.credential_version == 1
    assert user.role == role
    assert user.password_hash != GOOD
    assert passwords.verify_password(user.password_hash, GOOD)
    assert not passwords.verify_password(user.password_hash, GOOD.strip())
    assert GOOD not in repr(user)
    assert repo.get_by_username("operator") == user


def test_duplicate_never_replaces_account(service, repo):
    original = service.create("operator", GOOD, "admin", now=100)
    with pytest.raises(AccountConflict):
        service.create("operator", GOOD + "other", "viewer", now=101)
    assert repo.get_by_username("operator") == original


@pytest.mark.parametrize("role,now", [("unknown", 100), ("admin", -1), ("admin", True), ("admin", 2**63)])
def test_bad_creation_leaves_no_account(service, db, role, now):
    with pytest.raises(ValueError):
        service.create("operator", GOOD, role, now=now)
    assert db.scalar("SELECT count(*) FROM admin_users") == 0


@pytest.mark.parametrize("operation", ["password", "role", "disable", "enable"])
def test_changes_revoke_only_target_sessions(service, repo, operation):
    first = service.create("operator", GOOD, "admin", now=100)
    other = service.create("other", GOOD, "viewer", now=100)
    assert repo.create_session(digest("first"), first.id, 1, 100, 300)
    assert repo.create_session(digest("other"), other.id, 1, 100, 300)
    if operation == "password":
        service.reset_password("operator", 1, GOOD + "nova")
    elif operation == "role":
        service.set_role("operator", 1, "viewer")
    else:
        service.set_active("operator", 1, operation == "enable")
    changed = repo.get_by_username("operator")
    assert changed.credential_version == 2
    assert repo.get_valid_session(digest("first"), 101, 100) is None
    assert repo.get_valid_session(digest("other"), 101, 100)
    if operation == "password":
        assert passwords.verify_password(changed.password_hash, GOOD + "nova")
        assert not passwords.verify_password(changed.password_hash, GOOD)
    else:
        assert changed.password_hash == first.password_hash


def test_reset_does_not_reactivate_or_promote(service, repo):
    service.create("operator", GOOD, "viewer", now=100)
    service.set_active("operator", 1, False)
    service.reset_password("operator", 2, GOOD + "nova")
    changed = repo.get_by_username("operator")
    assert not changed.is_active and changed.role == "viewer"
    assert changed.credential_version == 3


@pytest.mark.parametrize("operation", ["password", "role", "active"])
def test_stale_snapshot_does_not_overwrite(service, repo, operation):
    original = service.create("operator", GOOD, "admin", now=100)
    with pytest.raises(AccountConflict):
        if operation == "password":
            service.reset_password("operator", 2, GOOD + "nova")
        elif operation == "role":
            service.set_role("operator", 2, "viewer")
        else:
            service.set_active("operator", 2, False)
    assert repo.get_by_username("operator") == original


def test_conflict_during_hashing_is_not_retried(service, repo, monkeypatch):
    user = service.create("operator", GOOD, "admin", now=100)
    original_hash = passwords.hash_password
    def racing_hash(password):
        assert repo.update_user(user.id, 1, user.password_hash, "viewer", False)
        return original_hash(password)
    monkeypatch.setattr(passwords, "hash_password", racing_hash)
    with pytest.raises(AccountConflict):
        service.reset_password("operator", 1, GOOD + "nova")
    changed = repo.get_by_username("operator")
    assert changed.role == "viewer" and not changed.is_active
    assert changed.credential_version == 2 and changed.password_hash == user.password_hash


def test_revoke_sessions_is_idempotent_without_changing_credentials(service, repo):
    user = service.create("operator", GOOD, "admin", now=100)
    assert repo.create_session(digest("session"), user.id, 1, 100, 300)
    assert service.revoke_sessions("operator") == 1
    assert service.revoke_sessions("operator") == 0
    assert repo.get_by_username("operator") == user


@pytest.mark.parametrize("version", [None, True, 0, -1, 2**63])
def test_invalid_expected_version(service, version):
    with pytest.raises(ValueError):
        service.reset_password("operator", version, GOOD)


def test_absent_account_is_not_created_by_reset(service, db):
    with pytest.raises(AccountConflict):
        service.reset_password("absent", 1, GOOD)
    with pytest.raises(AccountConflict):
        service.revoke_sessions("absent")
    assert db.scalar("SELECT count(*) FROM admin_users") == 0


def test_invalid_changes_preserve_sessions(service, repo):
    user = service.create("operator", GOOD, "admin", now=100)
    assert repo.create_session(digest("session"), user.id, 1, 100, 300)
    for action in [lambda: service.reset_password("operator", 1, BLOCKED),
                   lambda: service.set_role("operator", 1, "unknown"),
                   lambda: service.set_active("operator", 1, 1)]:
        with pytest.raises(ValueError):
            action()
    assert repo.get_by_username("operator") == user
    assert repo.get_valid_session(digest("session"), 101, 100)


def test_database_uniqueness_still_protects_creation_race(service, repo, db, monkeypatch):
    original = service.create("operator", GOOD, "admin", now=100)
    monkeypatch.setattr(repo, "get_by_username", lambda username: None)
    with pytest.raises(db.integrity_error):
        service.create("operator", GOOD + "other", "viewer", now=101)
    assert repo.get_user(original.id) == original
    assert db.scalar("SELECT count(*) FROM admin_users") == 1


def test_hashing_failure_does_not_change_account_or_sessions(service, repo, monkeypatch):
    user = service.create("operator", GOOD, "admin", now=100)
    assert repo.create_session(digest("session"), user.id, 1, 100, 300)
    def fail(password):
        raise RuntimeError("hashing unavailable")
    monkeypatch.setattr(passwords, "hash_password", fail)
    with pytest.raises(RuntimeError, match="hashing unavailable"):
        service.reset_password("operator", 1, GOOD + "nova")
    assert repo.get_by_username("operator") == user
    assert repo.get_valid_session(digest("session"), 101, 100)
