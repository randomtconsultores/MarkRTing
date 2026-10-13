import sqlite3

from mini_marketo.database import SCHEMA_VERSION, connect, init_db
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
