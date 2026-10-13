import sqlite3
from pathlib import Path

from .migrations import Migration, MigrationError, latest_version, pending

# Esquema criado de raiz. As versões seguintes entram em MIGRATIONS (SQL do SQLite).
BASELINE_VERSION = 2
MIGRATIONS: list[Migration] = []
SCHEMA_VERSION = latest_version(BASELINE_VERSION, MIGRATIONS)

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

# Bases criadas antes de existir user_version (esquema v1, com o email em `contacts`).
LEGACY_V1_TO_V2 = (
    "DROP INDEX IF EXISTS ix_contacts_company;\n"
    "ALTER TABLE contacts RENAME TO contacts_v1;\n"
    + CONTACTS_DDL
    + """
INSERT INTO contacts (id, first_name, last_name, company_id, created_at)
    SELECT id, first_name, last_name, company_id, created_at FROM contacts_v1 ORDER BY id;
INSERT INTO contact_emails (contact_id, email, is_primary)
    SELECT id, email, 1 FROM contacts_v1 ORDER BY id;
DROP TABLE contacts_v1;
"""
)


def connect(db_path: str | Path) -> sqlite3.Connection:
    if str(db_path) != ":memory:":
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    # timeout: com uma ligação por pedido, os escritores esperam pelo bloqueio do ficheiro.
    conn = sqlite3.connect(str(db_path), check_same_thread=False, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _contacts_has_email_column(conn: sqlite3.Connection) -> bool:
    return any(r["name"] == "email" for r in conn.execute("PRAGMA table_info(contacts)"))


def _orphan_contact_ids(conn: sqlite3.Connection) -> list[int]:
    rows = conn.execute(
        "SELECT c.id FROM contacts c LEFT JOIN companies co ON co.id = c.company_id "
        "WHERE c.company_id IS NOT NULL AND co.id IS NULL ORDER BY c.id"
    ).fetchall()
    return [r["id"] for r in rows]


def _script(steps: list[Migration]) -> str:
    """Todos os passos numa única transação: ou ficam todos aplicados, ou nenhum.

    Cada script pode terminar com ou sem ';': normaliza-se para um só, para que o
    PRAGMA seguinte nunca fique colado à última instrução do passo.
    """
    body = "".join(
        f"{sql.strip().rstrip(';')};\nPRAGMA user_version = {target};\n" for target, sql in steps
    )
    return f"BEGIN;\n{body}COMMIT;"


def init_db(conn: sqlite3.Connection) -> None:
    latest = latest_version(BASELINE_VERSION, MIGRATIONS)
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version > latest:
        raise MigrationError(
            f"A base de dados (versão {version}) é mais recente do que a aplicação (versão {latest})."
        )
    if 0 < version < BASELINE_VERSION:
        raise MigrationError(f"Versão do esquema desconhecida: {version}.")

    steps: list[Migration] = []
    if version == 0:
        if _contacts_has_email_column(conn):
            orphans = _orphan_contact_ids(conn)
            if orphans:
                ids = ", ".join(str(i) for i in orphans)
                raise MigrationError(
                    "Migração v1->v2 abortada: contactos com empresa inexistente "
                    f"(ids: {ids}). Corrija ou remova estes registos e volte a arrancar; "
                    "a base de dados não foi alterada."
                )
            steps.append((BASELINE_VERSION, LEGACY_V1_TO_V2))
        else:
            steps.append((BASELINE_VERSION, COMPANIES_DDL + CONTACTS_DDL))
        version = BASELINE_VERSION
    steps += pending(version, MIGRATIONS)
    if not steps:
        return
    try:
        conn.executescript(_script(steps))
    except Exception:
        if conn.in_transaction:
            conn.rollback()
        raise
