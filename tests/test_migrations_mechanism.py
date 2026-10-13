import pytest

from mini_marketo.migrations import MigrationError


def test_fresh_database_records_current_version_and_init_is_idempotent(db):
    db.module.init_db(db.conn)
    db.module.init_db(db.conn)
    assert db.version() == db.module.SCHEMA_VERSION


def test_pending_migrations_are_applied_in_order(db, monkeypatch):
    latest = db.module.SCHEMA_VERSION
    monkeypatch.setattr(
        db.module,
        "MIGRATIONS",
        [
            *db.module.MIGRATIONS,
            (latest + 1, "CREATE TABLE m_a (id INTEGER)"),
            (latest + 2, "INSERT INTO m_a (id) VALUES (7)"),
        ],
    )
    db.module.init_db(db.conn)
    assert db.version() == latest + 2
    assert db.scalar("SELECT id FROM m_a") == 7


def test_failing_migration_rolls_back_every_pending_step(db, monkeypatch):
    latest = db.module.SCHEMA_VERSION
    monkeypatch.setattr(
        db.module,
        "MIGRATIONS",
        [
            *db.module.MIGRATIONS,
            (latest + 1, "CREATE TABLE m_ok (id INTEGER)"),
            (latest + 2, "CREATE TABLE m_bad (id INTEGER); CREATE TABLE m_bad (id INTEGER)"),
        ],
    )
    # O erro tem de ser a tabela duplicada, não outra falha qualquer (ex.: sintaxe).
    with pytest.raises(Exception, match="m_bad"):
        db.module.init_db(db.conn)
    assert db.version() == latest
    with pytest.raises(Exception, match="m_ok"):
        db.execute("SELECT * FROM m_ok")


def test_database_newer_than_application_is_refused(db):
    db.set_version(db.module.SCHEMA_VERSION + 5)
    with pytest.raises(MigrationError, match="mais recente"):
        db.module.init_db(db.conn)
