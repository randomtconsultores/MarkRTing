from concurrent.futures import ThreadPoolExecutor
import hashlib
from threading import Event

import pytest

from mini_marketo.repositories.access_repository import AccessRepository


_WAIT_SECONDS = 10


def repository(kind, conn):
    if kind == "sqlite":
        return AccessRepository(conn)
    from mini_marketo.repositories.postgres_access_repository import PostgresAccessRepository
    return PostgresAccessRepository(conn)


class ScheduledConnection:
    """Delegate real SQL; synchronize only at an explicitly chosen statement."""

    def __init__(self, conn, prefix, reached, release=None):
        self.conn = conn
        self.prefix = prefix
        self.reached = reached
        self.release = release

    def __enter__(self):
        self.conn.__enter__()
        return self

    def __exit__(self, *args):
        return self.conn.__exit__(*args)

    def transaction(self):
        return self.conn.transaction()

    def execute(self, sql, params=()):
        matches = sql.startswith(self.prefix)
        if matches and self.release is None:
            self.reached.set()
        result = self.conn.execute(sql, params)
        if matches and self.release is not None:
            self.reached.set()
            if not self.release.wait(_WAIT_SECONDS):
                raise TimeoutError("First transaction was not released")
        return result


def overlap(db, first_action, first_prefix, second_action, second_prefix):
    held, attempted, release = Event(), Event(), Event()

    def run(action, prefix, reached, gate):
        handle = db.reopen()
        try:
            if handle.kind == "sqlite":
                handle.conn.execute("PRAGMA busy_timeout = 5000")
            else:
                handle.conn.execute("SET lock_timeout = '5s'")
                handle.conn.execute("SET statement_timeout = '8s'")
            conn = ScheduledConnection(handle.conn, prefix, reached, gate)
            return action(repository(handle.kind, conn))
        finally:
            handle.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(run, first_action, first_prefix, held, release)
        try:
            assert held.wait(_WAIT_SECONDS), "First statement was not reached"
            second = pool.submit(run, second_action, second_prefix, attempted, None)
            assert attempted.wait(_WAIT_SECONDS), "Second statement was not attempted"
            assert not first.done(), "First transaction should still be held"
        finally:
            release.set()
        return first.result(timeout=_WAIT_SECONDS), second.result(timeout=_WAIT_SECONDS)


@pytest.fixture
def seeded(db):
    repo = repository(db.kind, db.conn)
    account = repo.create_user("operator", "test-hash", "admin", 100)
    token_hash = hashlib.sha256(b"test-only-session").hexdigest()
    assert repo.create_session(token_hash, account.id, 1, 100, 300)
    return repo, account, token_hash


@pytest.mark.parametrize("change", ["session", "password", "role", "inactive"])
def test_revocation_started_first_prevents_session_refresh(db, seeded, change):
    repo, account, token_hash = seeded
    def revoke(r):
        if change == "session":
            return r.revoke_session(token_hash)
        return r.update_user(account.id, 1, "new-hash",
                             "viewer" if change == "role" else "admin",
                             change != "inactive")
    revoked, resolved = overlap(
        db,
        revoke, "DELETE FROM admin_sessions",
        lambda r: r.touch_valid_session(token_hash, 105, 20), "UPDATE admin_sessions",
    )
    assert revoked is True
    assert resolved is None
    assert repo.get_valid_session(token_hash, 105, 20) is None
    assert db.scalar("SELECT count(*) FROM admin_sessions") == 0


def test_resolution_started_first_does_not_prevent_later_revocation(db, seeded):
    repo, account, token_hash = seeded
    resolved, revoked = overlap(
        db,
        lambda r: r.touch_valid_session(token_hash, 105, 20), "UPDATE admin_sessions",
        lambda r: r.revoke_session(token_hash), "DELETE FROM admin_sessions",
    )
    assert resolved is not None
    assert resolved.last_seen_at == 105
    assert revoked is True
    assert repo.get_valid_session(token_hash, 106, 20) is None
    assert db.scalar("SELECT count(*) FROM admin_sessions") == 0


@pytest.mark.parametrize("first_time,second_time", [(105, 109), (109, 105)])
def test_concurrent_activity_never_moves_back(db, seeded, first_time, second_time):
    repo, account, token_hash = seeded
    first, second = overlap(
        db,
        lambda r: r.touch_valid_session(token_hash, first_time, 20), "UPDATE admin_sessions",
        lambda r: r.touch_valid_session(token_hash, second_time, 20), "UPDATE admin_sessions",
    )
    assert first is not None
    assert (second is not None) is (second_time >= first_time)
    stored = repo.get_valid_session(token_hash, 110, 20)
    assert stored.last_seen_at == max(first_time, second_time)
    assert stored.expires_at == 300


def test_credential_change_started_first_prevents_stale_session_creation(db, seeded):
    repo, account, token_hash = seeded
    other_hash = hashlib.sha256(b"new-test-only-session").hexdigest()
    changed, created = overlap(
        db,
        lambda r: r.update_user(account.id, 1, "new-hash", "admin", True),
        "DELETE FROM admin_sessions",
        lambda r: r.create_session(other_hash, account.id, 1, 105, 300),
        "INSERT INTO admin_sessions",
    )
    assert changed is True
    assert created is False
    assert repo.get_user(account.id).credential_version == 2
    assert repo.get_valid_session(token_hash, 106, 20) is None
    assert repo.get_valid_session(other_hash, 106, 20) is None
    assert db.scalar("SELECT count(*) FROM admin_sessions") == 0


def test_session_creation_started_first_is_revoked_by_later_credential_change(db, seeded):
    repo, account, token_hash = seeded
    other_hash = hashlib.sha256(b"another-test-only-session").hexdigest()
    created, changed = overlap(
        db,
        lambda r: r.create_session(other_hash, account.id, 1, 105, 300),
        "INSERT INTO admin_sessions",
        lambda r: r.update_user(account.id, 1, "new-hash", "admin", True),
        "UPDATE admin_users",
    )
    assert created is True
    assert changed is True
    assert repo.get_user(account.id).credential_version == 2
    assert db.scalar("SELECT count(*) FROM admin_sessions") == 0
