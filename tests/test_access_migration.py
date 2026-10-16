import sqlite3

import pytest


TABLES = (
    "companies", "contacts", "contact_emails", "consent_events", "segments",
    "campaigns", "email_messages", "suppressions", "subscription_requests",
    "subscription_request_purposes",
)


def snapshot(db):
    result = {}
    for table in TABLES:
        rows = db.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall()
        result[table] = [
            tuple(row.values()) if isinstance(row, dict) else tuple(row) for row in rows
        ]
    return result


def seed_existing_data(db):
    db.execute("INSERT INTO companies (name, website) VALUES ('Empresa teste', 'https://example.test')")
    company_id = db.scalar("SELECT max(id) FROM companies")
    p = db.p
    db.execute(
        f"INSERT INTO contacts (first_name, last_name, company_id) VALUES ({p}, {p}, {p})",
        ("Ana", "Silva", company_id),
    )
    contact_id = db.scalar("SELECT max(id) FROM contacts")
    db.execute(
        f"INSERT INTO contact_emails (contact_id, email, is_primary) VALUES ({p}, {p}, {db.true})",
        (contact_id, "ana@example.test"),
    )
    db.execute(
        "INSERT INTO consent_events (contact_id, purpose, decision, source, email) "
        f"VALUES ({p}, 'newsletter', 'granted', 'admin', 'ana@example.test')",
        (contact_id,),
    )
    db.execute(
        "INSERT INTO segments (name, description, match_mode, criteria) "
        "VALUES ('Teste', '', 'all', '[]')"
    )
    segment_id = db.scalar("SELECT max(id) FROM segments")
    db.execute(
        "INSERT INTO campaigns (name, subject, body_text, purpose, segment_id, segment_name) "
        f"VALUES ('Campanha teste', 'Assunto', 'Corpo', 'newsletter', {p}, 'Teste')",
        (segment_id,),
    )
    campaign_id = db.scalar("SELECT max(id) FROM campaigns")
    db.execute(
        "INSERT INTO email_messages (campaign_id, contact_id, email) "
        f"VALUES ({p}, {p}, 'ana@example.test')",
        (campaign_id, contact_id),
    )
    db.execute(
        "INSERT INTO suppressions (email, reason, source, detail) "
        "VALUES ('blocked@example.test', 'manual', 'admin', 'Teste')"
    )
    db.execute(
        "INSERT INTO subscription_requests (email, first_name, last_name, lang) "
        "VALUES ('pending@example.test', 'Rui', 'Costa', 'pt')"
    )
    request_id = db.scalar("SELECT max(id) FROM subscription_requests")
    db.execute(
        f"INSERT INTO subscription_request_purposes (request_id, purpose) VALUES ({p}, 'newsletter')",
        (request_id,),
    )


@pytest.fixture
def v8_db(db):
    assert db.module.SCHEMA_VERSION == 9, "Aplique a integração v9 antes destes testes."
    assert any(version == 9 for version, _ in db.module.MIGRATIONS)
    db.set_version(8)
    assert db.version() == 8
    seed_existing_data(db)
    return db


def test_v9_preserves_existing_data_and_reopens(v8_db):
    db = v8_db
    before = snapshot(db)
    again = db.reopen()
    try:
        assert again.version() == 9
        assert again.scalar("SELECT count(*) FROM admin_users") == 0
        assert again.scalar("SELECT count(*) FROM admin_sessions") == 0
        assert snapshot(again) == before
        # Apenas dados de teste de esquema; nao e um hash utilizavel para login.
        again.execute(
            "INSERT INTO admin_users (username, password_hash, role, created_at) "
            "VALUES ('admin', 'test-placeholder', 'admin', 100)"
        )
        user_id = again.scalar("SELECT id FROM admin_users WHERE username = 'admin'")
        p = again.p
        again.execute(
            "INSERT INTO admin_sessions "
            "(token_hash, user_id, credential_version, created_at, last_seen_at, expires_at) "
            f"VALUES ({p}, {p}, 1, 100, 100, 200)",
            ("a" * 64, user_id),
        )
        user_row = tuple(again.execute("SELECT * FROM admin_users").fetchone())
        session_row = tuple(again.execute("SELECT * FROM admin_sessions").fetchone())
    finally:
        again.close()
    reopened = db.reopen()
    try:
        assert reopened.version() == 9
        assert reopened.scalar("SELECT count(*) FROM admin_users") == 1
        assert reopened.scalar("SELECT count(*) FROM admin_sessions") == 1
        assert tuple(reopened.execute("SELECT * FROM admin_users").fetchone()) == user_row
        assert tuple(reopened.execute("SELECT * FROM admin_sessions").fetchone()) == session_row
        assert snapshot(reopened) == before
    finally:
        reopened.close()


def test_v9_failure_rolls_back_tables_and_version(v8_db, monkeypatch):
    db = v8_db
    before = snapshot(db)
    broken = [
        (version, sql + "\nINVALID ACCESS MIGRATION;" if version == 9 else sql)
        for version, sql in db.module.MIGRATIONS
    ]
    error_type = (
        sqlite3.OperationalError if db.kind == "sqlite" else db.module.psycopg.errors.SyntaxError
    )
    with monkeypatch.context() as patch:
        patch.setattr(db.module, "MIGRATIONS", broken)
        with pytest.raises(error_type):
            db.module.init_db(db.conn)
    assert db.version() == 8
    assert snapshot(db) == before
    if db.kind == "sqlite":
        assert db.scalar(
            "SELECT count(*) FROM sqlite_master WHERE type = 'table' "
            "AND name IN ('admin_users', 'admin_sessions')"
        ) == 0
    else:
        assert db.scalar("SELECT to_regclass('admin_users') IS NULL")
        assert db.scalar("SELECT to_regclass('admin_sessions') IS NULL")
    db.module.init_db(db.conn)
    assert db.version() == 9
    assert db.scalar("SELECT count(*) FROM admin_users") == 0
    assert db.scalar("SELECT count(*) FROM admin_sessions") == 0
    assert snapshot(db) == before
