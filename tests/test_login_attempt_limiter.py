from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from mini_marketo.repositories.access_repository import AccessRepository
from mini_marketo.services.login_attempt_limiter import (
    LoginAttemptConfig,
    LoginAttemptLimiter,
)


def make_repository(kind, conn):
    if kind == "sqlite":
        return AccessRepository(conn)
    from mini_marketo.repositories.postgres_access_repository import (
        PostgresAccessRepository,
    )

    return PostgresAccessRepository(conn)


def config(**overrides):
    values = {
        "hmac_key": b"test-only-login-budget-key-32bytes",
        "window_seconds": 60,
        "account_limit": 2,
        "ip_limit": 3,
        "installation_limit": 10,
    }
    values.update(overrides)
    return LoginAttemptConfig(**values)


@pytest.fixture
def limiter(db):
    return LoginAttemptLimiter(make_repository(db.kind, db.conn), config=config())


def test_all_three_budgets_are_persisted_as_context_separated_hmacs(limiter, db):
    username = "Sensitive.User@example.test"
    remote_ip = "2001:0db8:0:0:0:0:0:1"

    result = limiter.reserve(username, remote_ip, now=100)

    assert (result.allowed, result.retry_after) == (True, 0)
    rows = db.execute(
        "SELECT bucket_hash FROM admin_login_attempts ORDER BY bucket_hash"
    ).fetchall()
    hashes = {
        row["bucket_hash"] if isinstance(row, dict) else row[0]
        for row in rows
    }
    assert len(hashes) == 3
    assert all(len(value) == 64 and set(value) <= set("0123456789abcdef") for value in hashes)
    stored = " ".join(hashes)
    assert username.lower() not in stored
    assert "2001:db8::1" not in stored


def test_account_key_is_normalized_only_for_budget_and_denial_is_generic(limiter, db):
    assert limiter.reserve("Admin", "192.0.2.1", now=100).allowed
    assert limiter.reserve("admin", "2001:db8::1", now=100).allowed

    denied = limiter.reserve("ADMIN", "192.0.2.2", now=100)

    assert (denied.allowed, denied.retry_after) == (False, 60)
    assert db.scalar("SELECT count(*) FROM admin_login_attempts") == 4
    assert db.scalar("SELECT max(attempts) FROM admin_login_attempts") == 2


def test_separate_limiter_instances_share_persistent_budgets(db):
    policy = config(account_limit=1)
    first = LoginAttemptLimiter(make_repository(db.kind, db.conn), config=policy)
    assert first.reserve("Operator", "192.0.2.1", now=100).allowed

    reopened = db.reopen()
    try:
        second = LoginAttemptLimiter(
            make_repository(reopened.kind, reopened.conn), config=policy
        )
        denied = second.reserve("operator", "2001:db8::1", now=100)
    finally:
        reopened.close()

    assert (denied.allowed, denied.retry_after) == (False, 60)


def test_ip_and_installation_budgets_are_enforced_atomically(db):
    repo = make_repository(db.kind, db.conn)
    limiter = LoginAttemptLimiter(
        repo, config=config(account_limit=20, ip_limit=2, installation_limit=3)
    )
    assert limiter.reserve("one", "192.0.2.1", now=100).allowed
    assert limiter.reserve("two", "192.0.2.1", now=100).allowed

    denied_ip = limiter.reserve("three", "192.0.2.1", now=100)

    assert (denied_ip.allowed, denied_ip.retry_after) == (False, 60)
    assert db.scalar("SELECT count(*) FROM admin_login_attempts") == 4

    assert limiter.reserve("three", "192.0.2.2", now=100).allowed
    denied_installation = limiter.reserve("four", "192.0.2.3", now=100)

    assert (denied_installation.allowed, denied_installation.retry_after) == (False, 60)
    assert db.scalar("SELECT count(*) FROM admin_login_attempts") == 6


def test_expiry_boundary_starts_a_fresh_window(limiter, db):
    assert limiter.reserve("operator", "192.0.2.1", now=100).allowed
    assert limiter.reserve("operator", "192.0.2.2", now=100).allowed
    assert not limiter.reserve("operator", "192.0.2.3", now=100).allowed

    result = limiter.reserve("operator", "192.0.2.3", now=160)

    assert (result.allowed, result.retry_after) == (True, 0)
    account_hash = limiter._bucket_hash("account", b"operator")
    row = db.execute(
        "SELECT started_at, expires_at, attempts FROM admin_login_attempts "
        "WHERE bucket_hash = ? " if db.kind == "sqlite" else
        "SELECT started_at, expires_at, attempts FROM admin_login_attempts "
        "WHERE bucket_hash = %s",
        (account_hash,),
    ).fetchone()
    values = tuple(row.values()) if isinstance(row, dict) else tuple(row)
    assert values == (160, 220, 1)


def test_clock_before_window_start_is_rejected_without_increment(limiter, db):
    assert limiter.reserve("operator", "192.0.2.1", now=100).allowed
    before = db.scalar("SELECT sum(attempts) FROM admin_login_attempts")

    result = limiter.reserve("operator", "192.0.2.1", now=99)

    assert (result.allowed, result.retry_after) == (False, 61)
    assert db.scalar("SELECT sum(attempts) FROM admin_login_attempts") == before


def test_cleanup_deletes_only_expired_buckets(limiter, db):
    assert limiter.reserve("operator", "192.0.2.1", now=100).allowed
    assert limiter.cleanup_expired(now=159) == 0
    assert limiter.cleanup_expired(now=160) == 3
    assert db.scalar("SELECT count(*) FROM admin_login_attempts") == 0


def test_sql_constraints_reject_invalid_attempt_rows(db):
    error = db.integrity_error
    p = db.p
    for bucket_hash, start, expiry, attempts in (
        ("A" * 64, 100, 200, 1),
        ("a" * 64, -1, 200, 1),
        ("b" * 64, 100, 100, 1),
        ("c" * 64, 100, 200, 0),
    ):
        with pytest.raises(error):
            db.execute(
                "INSERT INTO admin_login_attempts "
                f"(bucket_hash, started_at, expires_at, attempts) VALUES ({p}, {p}, {p}, {p})",
                (bucket_hash, start, expiry, attempts),
            )


def test_configuration_is_mandatory_and_validated():
    with pytest.raises(ValueError, match="obrigatoria"):
        LoginAttemptConfig.from_env({})
    env = {
        "MINI_MARKETO_LOGIN_ATTEMPT_HMAC_KEY": "k" * 32,
        "MINI_MARKETO_LOGIN_ATTEMPT_WINDOW_SECONDS": "60",
        "MINI_MARKETO_LOGIN_ATTEMPT_ACCOUNT_LIMIT": "5",
        "MINI_MARKETO_LOGIN_ATTEMPT_IP_LIMIT": "20",
        "MINI_MARKETO_LOGIN_ATTEMPT_INSTALLATION_LIMIT": "100",
    }
    assert LoginAttemptConfig.from_env(env) == LoginAttemptConfig(
        b"k" * 32, 60, 5, 20, 100
    )
    with pytest.raises(ValueError, match="32 bytes"):
        LoginAttemptConfig(b"short", 60, 5, 20, 100)
    with pytest.raises(ValueError, match="inteiro positivo"):
        LoginAttemptConfig.from_env({**env, "MINI_MARKETO_LOGIN_ATTEMPT_IP_LIMIT": "0"})


@pytest.mark.parametrize("now", [-1, True, 1.0, 2**63])
def test_invalid_time_is_rejected_before_database_access(limiter, now):
    with pytest.raises(ValueError):
        limiter.reserve("operator", "192.0.2.1", now=now)


def test_concurrent_reservations_never_exceed_account_limit(db):
    count = 12
    barrier = Barrier(count)
    config_value = config(account_limit=4, ip_limit=100, installation_limit=100)

    def reserve_one(_):
        handle = db.reopen()
        try:
            if handle.kind == "sqlite":
                handle.conn.execute("PRAGMA busy_timeout = 10000")
            repo = make_repository(handle.kind, handle.conn)
            limiter = LoginAttemptLimiter(repo, config=config_value)
            barrier.wait(timeout=10)
            return limiter.reserve("shared-account", "192.0.2.1", now=100).allowed
        finally:
            handle.close()

    with ThreadPoolExecutor(max_workers=count) as pool:
        results = list(pool.map(reserve_one, range(count)))

    assert sum(results) == 4
    assert db.scalar("SELECT max(attempts) FROM admin_login_attempts") == 4
