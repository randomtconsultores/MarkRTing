"""Focused regressions for secret repr, INTEGER budgets and bounded cleanup."""

import pytest

from mini_marketo.repositories.access_repository import AccessRepository, MAX_LOGIN_ATTEMPTS
from mini_marketo.services.login_attempt_limiter import LoginAttemptConfig, LoginAttemptLimiter


def config(**changes):
    values = dict(hmac_key=b"test-only-secret-" * 3, window_seconds=60,
                  account_limit=2, ip_limit=3, installation_limit=10)
    values.update(changes)
    return LoginAttemptConfig(**values)


@pytest.fixture
def repo(db):
    if db.kind == "sqlite":
        return AccessRepository(db.conn)
    from mini_marketo.repositories.postgres_access_repository import PostgresAccessRepository
    return PostgresAccessRepository(db.conn)


def test_config_repr_does_not_expose_hmac_key():
    value = config()
    assert "hmac_key" not in repr(value)
    assert value.hmac_key.decode() not in repr(value)
    assert "window_seconds=60" in repr(value)


@pytest.mark.parametrize("name", ["account_limit", "ip_limit", "installation_limit"])
def test_config_rejects_limits_larger_than_schema_integer(name):
    with pytest.raises(ValueError):
        config(**{name: MAX_LOGIN_ATTEMPTS + 1})


def test_config_accepts_largest_supported_limits():
    value = config(account_limit=MAX_LOGIN_ATTEMPTS, ip_limit=MAX_LOGIN_ATTEMPTS,
                   installation_limit=MAX_LOGIN_ATTEMPTS)
    assert value.installation_limit == 2**31 - 1


def test_repository_rejects_overflow_limit_without_writing(repo, db):
    with pytest.raises(ValueError):
        repo.reserve_login_attempts([("a" * 64, MAX_LOGIN_ATTEMPTS + 1)], 100, 60)
    assert db.scalar("SELECT count(*) FROM admin_login_attempts") == 0


def test_last_integer_attempt_is_admitted_then_saturates(repo, db):
    budgets = [("a" * 64, MAX_LOGIN_ATTEMPTS)]
    assert repo.reserve_login_attempts(budgets, 100, 60).allowed
    db.execute(f"UPDATE admin_login_attempts SET attempts = {db.p}", (MAX_LOGIN_ATTEMPTS - 1,))
    assert repo.reserve_login_attempts(budgets, 100, 60).allowed
    denied = repo.reserve_login_attempts(budgets, 100, 60)
    assert (denied.allowed, denied.retry_after) == (False, 60)
    assert db.scalar("SELECT attempts FROM admin_login_attempts") == MAX_LOGIN_ATTEMPTS


def test_timestamps_keep_their_64_bit_range(repo, db):
    now = 2**40
    assert repo.reserve_login_attempts([("a" * 64, 1)], now, 60).allowed
    assert db.scalar("SELECT expires_at FROM admin_login_attempts") == now + 60


def test_service_cleanup_is_batched_and_keeps_active_buckets(repo, db):
    for number in range(1, 6):
        assert repo.reserve_login_attempts([(f"{number:064x}", 1)], 100, 10).allowed
    for number in (6, 7):
        assert repo.reserve_login_attempts([(f"{number:064x}", 1)], 105, 50).allowed
    service = LoginAttemptLimiter(repo, config=config())
    assert service.cleanup_expired(now=110, batch_size=2) == 2
    hashes = [r["bucket_hash"] for r in db.execute(
        "SELECT bucket_hash FROM admin_login_attempts ORDER BY bucket_hash").fetchall()]
    assert hashes == [f"{number:064x}" for number in range(3, 8)]
    assert service.cleanup_expired(now=110, batch_size=2) == 2
    assert service.cleanup_expired(now=110, batch_size=2) == 1
    assert service.cleanup_expired(now=110, batch_size=2) == 0
    assert db.scalar("SELECT count(*) FROM admin_login_attempts") == 2


def test_cleanup_keeps_a_bucket_renewed_after_expiry(repo, db):
    assert repo.reserve_login_attempts([("a" * 64, 1)], 100, 10).allowed
    assert repo.reserve_login_attempts([("b" * 64, 1)], 100, 10).allowed
    assert repo.reserve_login_attempts([("a" * 64, 1)], 110, 10).allowed
    assert repo.cleanup_expired_login_attempts(110, batch_size=2) == 1
    assert db.scalar("SELECT bucket_hash FROM admin_login_attempts") == "a" * 64


@pytest.mark.parametrize("size", [0, -1, True, 1.5, None, 10001])
def test_invalid_cleanup_batch_does_not_delete(repo, db, size):
    assert repo.reserve_login_attempts([("a" * 64, 1)], 100, 10).allowed
    with pytest.raises(ValueError):
        repo.cleanup_expired_login_attempts(110, batch_size=size)
    assert db.scalar("SELECT count(*) FROM admin_login_attempts") == 1


@pytest.mark.parametrize("now", [-1, True, 2**63])
def test_invalid_cleanup_time_does_not_delete(repo, db, now):
    assert repo.reserve_login_attempts([("a" * 64, 1)], 100, 10).allowed
    with pytest.raises(ValueError):
        repo.cleanup_expired_login_attempts(now, batch_size=2)
    assert db.scalar("SELECT count(*) FROM admin_login_attempts") == 1
