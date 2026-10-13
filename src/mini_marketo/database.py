import sqlite3
from pathlib import Path

SCHEMA_VERSION = 2

COMPANIES_DDL = """
CREATE TABLE IF NOT EXISTS companies (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    name    TEXT NOT NULL CHECK (length(trim(name)) > 0),
    website TEXT NOT NULL DEFAULT ''
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_companies_name ON companies (lower(name));
"""

CONTACTS_DDL = """
CREATE TABLE contacts (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    first_name TEXT NOT NULL,
    last_name  TEXT NOT NULL,
    company_id INTEGER REFERENCES companies (id) ON DELETE SET NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%d %H:%M:%S', 'now'))
);
CREATE INDEX ix_contacts_company ON contacts (company_id);

CREATE TABLE contact_emails (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    contact_id INTEGER NOT NULL REFERENCES contacts (id) ON DELETE CASCADE,
    email      TEXT NOT NULL UNIQUE CHECK (email = lower(trim(email))),
    is_primary INTEGER NOT NULL DEFAULT 0 CHECK (is_primary IN (0, 1))
);
CREATE INDEX ix_contact_emails_contact ON contact_emails (contact_id);
CREATE UNIQUE INDEX ux_contact_one_primary ON contact_emails (contact_id) WHERE is_primary = 1;
"""

MIGRATION_V1_TO_V2 = (
    "BEGIN;\n"
    "DROP INDEX IF EXISTS ix_contacts_company;\n"
    "ALTER TABLE contacts RENAME TO contacts_v1;\n"
    + CONTACTS_DDL
    + """
INSERT INTO contacts (id, first_name, last_name, company_id, created_at)
    SELECT id, first_name, last_name, company_id, created_at FROM contacts_v1;
INSERT INTO contact_emails (contact_id, email, is_primary)
    SELECT id, email, 1 FROM contacts_v1;
DROP TABLE contacts_v1;
PRAGMA user_version = 2;
COMMIT;
"""
)


def connect(db_path: str | Path) -> sqlite3.Connection:
    if str(db_path) != ":memory:":
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _contacts_has_email_column(conn: sqlite3.Connection) -> bool:
    return any(r["name"] == "email" for r in conn.execute("PRAGMA table_info(contacts)"))


def init_db(conn: sqlite3.Connection) -> None:
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version >= SCHEMA_VERSION:
        return
    try:
        if _contacts_has_email_column(conn):
            conn.executescript(MIGRATION_V1_TO_V2)
        else:
            conn.executescript(
                COMPANIES_DDL + CONTACTS_DDL + f"PRAGMA user_version = {SCHEMA_VERSION};"
            )
    except Exception:
        if conn.in_transaction:
            conn.rollback()
        raise
