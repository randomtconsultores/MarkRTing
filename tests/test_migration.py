import sqlite3

import pytest

from mini_marketo.database import SCHEMA_VERSION, MigrationError, connect, init_db
from mini_marketo.repositories.contact_repository import ContactRepository
from mini_marketo.services.contact_service import ContactService

V1_SCHEMA = """
CREATE TABLE companies (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    name    TEXT NOT NULL CHECK (length(trim(name)) > 0),
    website TEXT NOT NULL DEFAULT ''
);
CREATE UNIQUE INDEX ux_companies_name ON companies (lower(name));
CREATE TABLE contacts (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    email      TEXT NOT NULL UNIQUE CHECK (email = lower(trim(email))),
    first_name TEXT NOT NULL,
    last_name  TEXT NOT NULL,
    company_id INTEGER REFERENCES companies (id) ON DELETE SET NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%d %H:%M:%S', 'now'))
);
CREATE INDEX ix_contacts_company ON contacts (company_id);
"""


def make_v1_db(path):
    raw = sqlite3.connect(str(path))
    raw.executescript(V1_SCHEMA)
    raw.execute("INSERT INTO companies (name) VALUES ('ACME')")
    raw.execute(
        "INSERT INTO contacts (email, first_name, last_name, company_id) "
        "VALUES ('ana@a.pt', 'Ana', 'Silva', 1)"
    )
    raw.execute(
        "INSERT INTO contacts (email, first_name, last_name) VALUES ('rui@a.pt', 'Rui', 'Costa')"
    )
    raw.commit()
    raw.close()


def test_migration_preserves_contacts_and_makes_email_primary(tmp_path):
    db = tmp_path / "v1.db"
    make_v1_db(db)
    conn = connect(db)
    init_db(conn)
    service = ContactService(ContactRepository(conn))

    assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    by_id = {c.id: c for c in service.list_contacts()}
    assert by_id[1].emails == ("ana@a.pt",) and by_id[1].company_name == "ACME"
    assert by_id[2].emails == ("rui@a.pt",) and by_id[2].company_id is None
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []


def test_migration_is_idempotent_and_db_stays_usable(tmp_path):
    db = tmp_path / "v1.db"
    make_v1_db(db)
    conn = connect(db)
    init_db(conn)
    init_db(conn)
    service = ContactService(ContactRepository(conn))
    novo = service.create_contact("Eva", "Lima", "eva@a.pt")
    assert novo.id == 3
    service.add_email(1, "ana2@a.pt")
    assert service.repo.get_contact(1).emails == ("ana@a.pt", "ana2@a.pt")


def test_fresh_database_is_created_at_current_version():
    conn = connect(":memory:")
    init_db(conn)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    cols = [r["name"] for r in conn.execute("PRAGMA table_info(contacts)")]
    assert "email" not in cols


def test_migration_email_ids_follow_contact_order(tmp_path):
    db = tmp_path / "v1.db"
    raw = sqlite3.connect(str(db))
    raw.executescript(V1_SCHEMA)
    raw.execute(
        "INSERT INTO contacts (email, first_name, last_name) VALUES ('zed@a.pt', 'Zed', 'Z')"
    )
    raw.execute(
        "INSERT INTO contacts (email, first_name, last_name) VALUES ('abe@a.pt', 'Abe', 'A')"
    )
    raw.commit()
    raw.close()

    conn = connect(db)
    init_db(conn)
    rows = conn.execute("SELECT id, contact_id FROM contact_emails ORDER BY id").fetchall()
    assert [(r["id"], r["contact_id"]) for r in rows] == [(1, 1), (2, 2)]


def test_migration_aborts_clearly_on_orphan_company_and_leaves_v1_untouched(tmp_path):
    db = tmp_path / "v1.db"
    make_v1_db(db)
    raw = sqlite3.connect(str(db))  # foreign_keys desligado por omissao: aceita o orfao
    raw.execute(
        "INSERT INTO contacts (email, first_name, last_name, company_id) "
        "VALUES ('orfao@a.pt', 'Orfao', 'X', 9999)"
    )
    raw.commit()
    raw.close()

    conn = connect(db)
    with pytest.raises(MigrationError, match="ids: 3"):
        init_db(conn)

    assert conn.execute("PRAGMA user_version").fetchone()[0] == 0
    tables = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "contact_emails" not in tables and "contacts_v1" not in tables
    cols = [r["name"] for r in conn.execute("PRAGMA table_info(contacts)")]
    assert "email" in cols
    assert conn.execute("SELECT COUNT(*) FROM contacts").fetchone()[0] == 3


def test_migration_succeeds_after_orphan_is_fixed(tmp_path):
    db = tmp_path / "v1.db"
    make_v1_db(db)
    raw = sqlite3.connect(str(db))
    raw.execute(
        "INSERT INTO contacts (email, first_name, last_name, company_id) "
        "VALUES ('orfao@a.pt', 'Orfao', 'X', 9999)"
    )
    raw.commit()
    raw.close()

    conn = connect(db)
    with pytest.raises(MigrationError):
        init_db(conn)
    with conn:
        conn.execute("UPDATE contacts SET company_id = NULL WHERE id = 3")
    init_db(conn)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    assert conn.execute("SELECT COUNT(*) FROM contact_emails").fetchone()[0] == 3
