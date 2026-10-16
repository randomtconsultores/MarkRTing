import hashlib

import pytest

from mini_marketo.repositories.access_repository import AccessRepository


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


@pytest.fixture
def repo(db):
    if db.kind == "sqlite":
        return AccessRepository(db.conn)
    from mini_marketo.repositories.postgres_access_repository import PostgresAccessRepository
    return PostgresAccessRepository(db.conn)


def user(repo, name="operator"):
    return repo.create_user(name, "test-hash-not-a-password", "admin", 100)


def test_create_lookup_and_sensitive_repr(repo):
    record = user(repo)
    assert repo.get_user(record.id) == record
    assert repo.get_by_username(record.username) == record
    assert record.is_active is True
    assert record.credential_version == 1
    assert record.password_hash not in repr(record)
    assert repo.get_user(999999) is None
    assert repo.get_by_username("absent") is None


def test_duplicate_user_does_not_break_repository(repo, db):
    record = user(repo)
    with pytest.raises(db.integrity_error):
        user(repo)
    assert repo.get_user(record.id) == record
    assert user(repo, "other").id != record.id


@pytest.mark.parametrize("role,active", [("viewer", True), ("admin", False)])
def test_user_update_revokes_only_their_sessions(repo, role, active):
    first = user(repo)
    other = user(repo, "other")
    for value, owner in [("a", first), ("b", first), ("c", other)]:
        assert repo.create_session(digest(value), owner.id, 1, 100, 300)
    assert repo.update_user(first.id, 1, "changed-hash", role, active)
    changed = repo.get_user(first.id)
    assert (changed.credential_version, changed.role, changed.is_active) == (2, role, active)
    assert changed.password_hash == "changed-hash"
    assert repo.get_valid_session(digest("a"), 101, 100) is None
    assert repo.get_valid_session(digest("b"), 101, 100) is None
    assert repo.get_valid_session(digest("c"), 101, 100) is not None


def test_password_update_alone_revokes_sessions(repo):
    record = user(repo)
    assert repo.create_session(digest("a"), record.id, 1, 100, 300)
    assert repo.update_user(record.id, 1, "changed-hash", "admin", True)
    assert repo.get_valid_session(digest("a"), 101, 100) is None
    assert not repo.create_session(digest("b"), record.id, 1, 101, 300)
    assert repo.create_session(digest("b"), record.id, 2, 101, 300)


def test_stale_update_preserves_user_and_session(repo):
    record = user(repo)
    assert repo.create_session(digest("a"), record.id, 1, 100, 300)
    assert not repo.update_user(record.id, 2, "new", "viewer", False)
    assert not repo.update_user(999999, 1, "new", "viewer", False)
    assert repo.get_user(record.id) == record
    assert repo.get_valid_session(digest("a"), 101, 100)


@pytest.mark.parametrize("now,idle,valid", [
    (99, 100, False), (100, 100, True), (109, 10, True),
    (110, 10, False), (299, 1000, True), (300, 1000, False),
])
def test_session_expiry_boundaries(repo, now, idle, valid):
    record = user(repo)
    assert repo.create_session(digest("a"), record.id, 1, 100, 300)
    session = repo.get_valid_session(digest("a"), now, idle)
    assert (session is not None) is valid
    if session:
        assert digest("a") not in repr(session)


def test_absent_inactive_or_stale_user_cannot_create_session(repo):
    assert not repo.create_session(digest("a"), 999999, 1, 100, 300)
    record = user(repo)
    assert not repo.create_session(digest("a"), record.id, 2, 100, 300)
    assert repo.update_user(record.id, 1, "new", "admin", False)
    assert not repo.create_session(digest("a"), record.id, 2, 100, 300)


def test_duplicate_session_and_invalid_dates(repo, db):
    record = user(repo)
    assert repo.create_session(digest("a"), record.id, 1, 100, 300)
    with pytest.raises(db.integrity_error):
        repo.create_session(digest("a"), record.id, 1, 100, 300)
    with pytest.raises(db.integrity_error):
        repo.create_session(digest("b"), record.id, 1, 100, 100)
    assert repo.get_valid_session(digest("a"), 101, 100)


@pytest.mark.parametrize("value", ["raw-token", "g" * 64, "A" * 64, None])
def test_invalid_digest_is_rejected(repo, value):
    with pytest.raises(ValueError):
        repo.create_session(value, 1, 1, 100, 300)
    with pytest.raises(ValueError):
        repo.get_valid_session(value, 101, 100)
    with pytest.raises(ValueError):
        repo.revoke_session(value)


def test_revocation_is_idempotent_and_isolated(repo):
    first = user(repo)
    other = user(repo, "other")
    assert repo.create_session(digest("a"), first.id, 1, 100, 300)
    assert repo.create_session(digest("b"), other.id, 1, 100, 300)
    assert repo.revoke_session(digest("a"))
    assert not repo.revoke_session(digest("a"))
    assert repo.get_valid_session(digest("a"), 101, 100) is None
    assert repo.get_valid_session(digest("b"), 101, 100)
    assert repo.revoke_user_sessions(other.id) == 1
    assert repo.revoke_user_sessions(other.id) == 0


def test_invalid_user_update_rolls_back_without_revoking(repo, db):
    record = user(repo)
    assert repo.create_session(digest("a"), record.id, 1, 100, 300)
    with pytest.raises(db.integrity_error):
        repo.update_user(record.id, 1, "new", "unknown", True)
    assert repo.get_user(record.id) == record
    assert repo.get_valid_session(digest("a"), 101, 100)


class FailSessionDelete:
    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        self.conn.__enter__()
        return self

    def __exit__(self, *args):
        return self.conn.__exit__(*args)

    def transaction(self):
        return self.conn.transaction()

    def execute(self, sql, params=()):
        if sql.startswith("DELETE FROM admin_sessions"):
            raise RuntimeError("Simulated revocation failure")
        return self.conn.execute(sql, params)


def test_user_update_and_revocation_are_atomic(repo):
    record = user(repo)
    assert repo.create_session(digest("a"), record.id, 1, 100, 300)
    failing = type(repo)(FailSessionDelete(repo.conn))
    with pytest.raises(RuntimeError, match="Simulated revocation failure"):
        failing.update_user(record.id, 1, "new", "viewer", False)
    assert repo.get_user(record.id) == record
    assert repo.get_valid_session(digest("a"), 101, 100)
