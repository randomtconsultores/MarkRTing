import hashlib

import pytest

from mini_marketo.repositories.access_repository import AccessRepository
from mini_marketo.services.session_service import SessionService


@pytest.fixture
def repo(db):
    if db.kind == "sqlite":
        return AccessRepository(db.conn)
    from mini_marketo.repositories.postgres_access_repository import PostgresAccessRepository
    return PostgresAccessRepository(db.conn)


@pytest.fixture
def sessions(repo):
    return SessionService(repo, absolute_seconds=100, idle_seconds=10)


@pytest.fixture
def account(repo):
    return repo.create_user("operator", "test-hash", "admin", 100)


def digest(token):
    return hashlib.sha256(token.encode("ascii")).hexdigest()


def test_issue_stores_only_digest(sessions, repo, account, db):
    issued = sessions.issue(account.id, account.credential_version, now=100)
    assert issued.expires_at == 200
    assert len(issued.token) == 43
    assert issued.token not in repr(issued)
    stored = repo.get_valid_session(digest(issued.token), 100, 10)
    assert (stored.user_id, stored.created_at, stored.last_seen_at, stored.expires_at) == (account.id, 100, 100, 200)
    rows = db.execute("SELECT * FROM admin_sessions").fetchall()
    assert issued.token not in str([dict(row) for row in rows])


def test_fresh_token_for_each_issue(sessions, account):
    first = sessions.issue(account.id, 1, now=100)
    second = sessions.issue(account.id, 1, now=100)
    assert first.token != second.token
    assert sessions.resolve(first.token, now=100)
    assert sessions.resolve(second.token, now=100)


def test_activity_refresh_without_extending_absolute_expiry(sessions, account):
    issued = sessions.issue(account.id, 1, now=100)
    for now in range(109, 200, 9):
        record = sessions.resolve(issued.token, now=now)
        assert record.last_seen_at == now
        assert record.expires_at == 200
    assert sessions.resolve(issued.token, now=200) is None


@pytest.mark.parametrize("now", [99, 110, 200, 201])
def test_invalid_session_not_refreshed(sessions, repo, account, db, now):
    issued = sessions.issue(account.id, 1, now=100)
    assert sessions.resolve(issued.token, now=now) is None
    assert db.scalar("SELECT last_seen_at FROM admin_sessions") == 100
    assert repo.get_valid_session(digest(issued.token), 100, 10)


def test_same_second_allowed_but_time_never_moves_back(sessions, account, db):
    issued = sessions.issue(account.id, 1, now=100)
    assert sessions.resolve(issued.token, now=105)
    assert sessions.resolve(issued.token, now=105)
    assert sessions.resolve(issued.token, now=104) is None
    assert db.scalar("SELECT last_seen_at FROM admin_sessions") == 105


def test_issue_rejects_absent_inactive_and_stale_account(sessions, repo, account):
    assert sessions.issue(999999, 1, now=100) is None
    assert sessions.issue(account.id, 2, now=100) is None
    assert repo.update_user(account.id, 1, "changed", "admin", False)
    assert sessions.issue(account.id, 1, now=100) is None
    assert sessions.issue(account.id, 2, now=100) is None


@pytest.mark.parametrize("role,active", [("admin", True), ("viewer", True), ("admin", False)])
def test_user_change_invalidates_session(sessions, repo, account, role, active):
    issued = sessions.issue(account.id, 1, now=100)
    assert repo.update_user(account.id, 1, "changed", role, active)
    assert sessions.resolve(issued.token, now=101) is None
    assert sessions.issue(account.id, 1, now=101) is None


@pytest.mark.parametrize("change", ["is_active = 0", "credential_version = 2"])
def test_touch_checks_user_even_if_session_was_not_deleted(sessions, account, db, change):
    issued = sessions.issue(account.id, 1, now=100)
    db.execute("UPDATE admin_users SET " + change)
    assert sessions.resolve(issued.token, now=101) is None
    assert db.scalar("SELECT last_seen_at FROM admin_sessions") == 100


def test_revocation_is_idempotent_and_isolated(sessions, account):
    first = sessions.issue(account.id, 1, now=100)
    second = sessions.issue(account.id, 1, now=100)
    assert sessions.revoke(first.token)
    assert not sessions.revoke(first.token)
    assert sessions.resolve(first.token, now=101) is None
    assert sessions.resolve(second.token, now=101)


@pytest.mark.parametrize("token", [None, 42, b"bytes", "", "x" * 42, "x" * 44, "é" * 43, "!" * 43])
def test_bad_token_rejected_without_repository_access(token):
    service = SessionService(object(), absolute_seconds=100, idle_seconds=10)
    assert service.resolve(token, now=100) is None
    assert not service.revoke(token)


def test_unknown_well_formed_token(sessions):
    assert sessions.resolve("A" * 43, now=100) is None
    assert not sessions.revoke("A" * 43)


@pytest.mark.parametrize("absolute,idle", [(0, 1), (100, 0), (100, -1), (100, True), (True, 1), (100.0, 1), (10, 11), (2**63, 1)])
def test_invalid_policy(absolute, idle):
    with pytest.raises(ValueError):
        SessionService(object(), absolute_seconds=absolute, idle_seconds=idle)


@pytest.mark.parametrize("now", [-1, True, 1.0, None, 2**63])
def test_invalid_time(sessions, account, now):
    with pytest.raises(ValueError):
        sessions.issue(account.id, 1, now=now)
    with pytest.raises(ValueError):
        sessions.resolve("A" * 43, now=now)


def test_expiry_overflow(sessions, account):
    with pytest.raises(ValueError):
        sessions.issue(account.id, 1, now=2**63 - 1)


def test_repository_failures_propagate(sessions, account, monkeypatch):
    def fail(*args):
        raise RuntimeError("database unavailable")
    monkeypatch.setattr(sessions.repo, "create_session", fail)
    with pytest.raises(RuntimeError, match="database unavailable"):
        sessions.issue(account.id, 1, now=100)
    monkeypatch.setattr(sessions.repo, "touch_valid_session", fail)
    with pytest.raises(RuntimeError, match="database unavailable"):
        sessions.resolve("A" * 43, now=100)


@pytest.mark.parametrize("now,idle", [(-1, 10), (True, 10), (100, 0), (100, True), (100, 2**63)])
def test_repository_touch_validates_arguments(repo, now, idle):
    with pytest.raises(ValueError):
        repo.touch_valid_session("a" * 64, now, idle)


def test_repository_touch_rejects_raw_token(repo):
    with pytest.raises(ValueError):
        repo.touch_valid_session("raw-token", 100, 10)


def test_touch_failure_rolls_back_activity(sessions, repo, account, db, monkeypatch):
    issued = sessions.issue(account.id, 1, now=100)
    def fail(*args):
        raise RuntimeError("simulated read failure")
    monkeypatch.setattr(repo, "get_valid_session", fail)
    with pytest.raises(RuntimeError, match="simulated read failure"):
        sessions.resolve(issued.token, now=105)
    assert db.scalar("SELECT last_seen_at FROM admin_sessions") == 100


def test_generator_uses_32_random_bytes_and_collision_does_not_overwrite(sessions, account, db, monkeypatch):
    from mini_marketo.services import session_service
    calls = []
    def fixed_token(size):
        calls.append(size)
        return "A" * 43
    monkeypatch.setattr(session_service.secrets, "token_urlsafe", fixed_token)
    first = sessions.issue(account.id, 1, now=100)
    with pytest.raises(db.integrity_error):
        sessions.issue(account.id, 1, now=101)
    assert calls == [32, 32]
    assert db.scalar("SELECT count(*) FROM admin_sessions") == 1
    assert sessions.resolve(first.token, now=101)


def test_invalid_generator_output_fails_closed(sessions, account, db, monkeypatch):
    from mini_marketo.services import session_service
    monkeypatch.setattr(session_service.secrets, "token_urlsafe", lambda size: "invalid")
    with pytest.raises(RuntimeError, match="formato invalido"):
        sessions.issue(account.id, 1, now=100)
    assert db.scalar("SELECT count(*) FROM admin_sessions") == 0
