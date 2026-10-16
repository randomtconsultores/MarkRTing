"""Restrições de acesso verificadas sobre o esquema criado pelas migrações."""

import hashlib

import pytest


@pytest.fixture
def access_db(db):
    return db


def add_user(db, username="operator", role="admin", active=1):
    p = db.p
    db.execute(
        "INSERT INTO admin_users "
        "(username, password_hash, role, is_active, created_at) "
        f"VALUES ({p}, {p}, {p}, {p}, {p})",
        (username, "placeholder-hash-not-a-password", role, active, 100),
    )
    return db.scalar(f"SELECT id FROM admin_users WHERE username = {p}", (username,))


def add_session(db, user_id, token_hash=None, last_seen=100, expires=200):
    digest = token_hash if token_hash is not None else hashlib.sha256(b"test-only").hexdigest()
    p = db.p
    db.execute(
        "INSERT INTO admin_sessions "
        "(token_hash, user_id, credential_version, created_at, last_seen_at, expires_at) "
        f"VALUES ({', '.join([p] * 6)})",
        (digest, user_id, 1, 100, last_seen, expires),
    )


def test_users_and_sessions_are_separate_from_contacts(access_db):
    db = access_db
    user_id = add_user(db)
    add_session(db, user_id)
    assert db.scalar("SELECT count(*) FROM admin_users") == 1
    assert db.scalar("SELECT count(*) FROM admin_sessions") == 1
    assert db.scalar("SELECT count(*) FROM contacts") == 0


@pytest.mark.parametrize("username,role,active", [
    ("", "admin", 1), (" Operator ", "admin", 1),
    ("operator", "owner", 1), ("operator", "admin", 2),
])
def test_invalid_user_is_rejected(access_db, username, role, active):
    with pytest.raises(access_db.integrity_error):
        add_user(access_db, username, role, active)


def test_duplicate_username_is_rejected(access_db):
    add_user(access_db)
    with pytest.raises(access_db.integrity_error):
        add_user(access_db)


@pytest.mark.parametrize("last_seen,expires", [(99, 200), (100, 100), (200, 200)])
def test_invalid_session_dates_are_rejected(access_db, last_seen, expires):
    user_id = add_user(access_db)
    with pytest.raises(access_db.integrity_error):
        add_session(access_db, user_id, last_seen=last_seen, expires=expires)


def test_session_requires_existing_user(access_db):
    with pytest.raises(access_db.integrity_error):
        add_session(access_db, 999999)


def test_duplicate_session_hash_is_rejected(access_db):
    user_id = add_user(access_db)
    add_session(access_db, user_id)
    with pytest.raises(access_db.integrity_error):
        add_session(access_db, user_id)


def test_short_session_hash_is_rejected(access_db):
    user_id = add_user(access_db)
    with pytest.raises(access_db.integrity_error):
        add_session(access_db, user_id, token_hash="short")
