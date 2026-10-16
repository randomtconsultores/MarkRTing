import hashlib

import pytest

from mini_marketo import passwords
from mini_marketo.repositories.access_repository import AccessRepository
from mini_marketo.services.authentication_service import AuthenticationService
from mini_marketo.services.session_service import SessionService


@pytest.fixture
def repo(db):
    if db.kind == "sqlite":
        return AccessRepository(db.conn)
    from mini_marketo.repositories.postgres_access_repository import PostgresAccessRepository
    return PostgresAccessRepository(db.conn)


@pytest.fixture(scope="module")
def stored_hash():
    return passwords.hash_password("integration-test-password")


@pytest.fixture
def account(repo, stored_hash):
    return repo.create_user("operator", stored_hash, "admin", 100)


def auth(repo):
    return AuthenticationService(repo, absolute_seconds=100, idle_seconds=20)


@pytest.mark.parametrize("role", ["admin", "viewer"])
def test_login_creates_resolvable_session_with_only_digest(repo, account, db, role):
    if role != account.role:
        assert repo.update_user(account.id, 1, account.password_hash, role, True)
    issued = auth(repo).authenticate("operator", "integration-test-password", now=105)
    assert issued is not None
    token_hash = hashlib.sha256(issued.token.encode()).hexdigest()
    stored = repo.get_valid_session(token_hash, 106, 20)
    assert stored.user_id == account.id
    assert stored.credential_version == repo.get_user(account.id).credential_version
    assert stored.expires_at == 205
    rows = db.execute("SELECT * FROM admin_sessions").fetchall()
    assert issued.token not in str([dict(row) for row in rows])
    sessions = SessionService(repo, absolute_seconds=100, idle_seconds=20)
    assert sessions.resolve(issued.token, now=106)


@pytest.mark.parametrize("failure", ["wrong-password", "absent", "inactive"])
def test_invalid_login_creates_no_session(repo, account, db, failure):
    username = "absent" if failure == "absent" else "operator"
    password = "wrong" if failure == "wrong-password" else "integration-test-password"
    if failure == "inactive":
        assert repo.update_user(account.id, 1, account.password_hash, "admin", False)
    assert auth(repo).authenticate(username, password, now=105) is None
    assert db.scalar("SELECT count(*) FROM admin_sessions") == 0


def test_failed_login_preserves_existing_session(repo, account):
    service = auth(repo)
    issued = service.authenticate("operator", "integration-test-password", now=105)
    assert service.authenticate("operator", "wrong", now=106) is None
    assert repo.get_valid_session(hashlib.sha256(issued.token.encode()).hexdigest(), 106, 20)


@pytest.mark.parametrize("change", ["password", "role", "inactive"])
def test_update_after_password_verification_rejects_stale_issuance(repo, account, db, monkeypatch, change):
    real_verify = passwords.verify_password
    def verify(stored, supplied):
        valid = real_verify(stored, supplied)
        assert repo.update_user(account.id, 1,
                                "changed-hash" if change == "password" else account.password_hash,
                                "viewer" if change == "role" else "admin",
                                change != "inactive")
        return valid
    monkeypatch.setattr(passwords, "verify_password", verify)
    assert auth(repo).authenticate("operator", "integration-test-password", now=105) is None
    assert repo.get_user(account.id).credential_version == 2
    assert db.scalar("SELECT count(*) FROM admin_sessions") == 0


def test_corrupt_stored_hash_cannot_authenticate(repo, account, db):
    assert repo.update_user(account.id, 1, "not-an-argon2-hash", "admin", True)
    assert auth(repo).authenticate("operator", "integration-test-password", now=105) is None
    assert db.scalar("SELECT count(*) FROM admin_sessions") == 0
