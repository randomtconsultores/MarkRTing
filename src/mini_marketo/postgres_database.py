import psycopg
from psycopg.rows import dict_row

from .database import MigrationError

SCHEMA_VERSION = 2
_LOCK_ID = 7305001

SCHEMA_V2 = """
CREATE TABLE schema_version (version INTEGER NOT NULL);

CREATE TABLE companies (
    id      INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name    TEXT NOT NULL CHECK (length(trim(name)) > 0),
    website TEXT NOT NULL DEFAULT ''
);
CREATE UNIQUE INDEX ux_companies_name ON companies (lower(name));

CREATE TABLE contacts (
    id         INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    first_name TEXT NOT NULL,
    last_name  TEXT NOT NULL,
    company_id INTEGER REFERENCES companies (id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ix_contacts_company ON contacts (company_id);

CREATE TABLE contact_emails (
    id         INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    contact_id INTEGER NOT NULL REFERENCES contacts (id) ON DELETE CASCADE,
    email      TEXT NOT NULL CONSTRAINT contact_emails_email_key UNIQUE
               CHECK (email = lower(trim(email))),
    is_primary BOOLEAN NOT NULL DEFAULT FALSE
);
CREATE INDEX ix_contact_emails_contact ON contact_emails (contact_id);
CREATE UNIQUE INDEX ux_contact_one_primary ON contact_emails (contact_id) WHERE is_primary;
"""


def connect(dsn: str, schema: str | None = None) -> psycopg.Connection:
    """Ligação em autocommit: cada escrita usa `with conn.transaction()`.

    `schema` serve para isolar testes (search_path); em produção fica None.
    """
    kwargs = {"autocommit": True, "row_factory": dict_row}
    if schema:
        kwargs["options"] = f"-c search_path={schema}"
    return psycopg.connect(dsn, **kwargs)


def init_db(conn: psycopg.Connection) -> None:
    with conn.transaction():
        conn.execute("SELECT pg_advisory_xact_lock(%s)", (_LOCK_ID,))
        exists = conn.execute(
            "SELECT to_regclass('schema_version') IS NOT NULL AS ok"
        ).fetchone()["ok"]
        if not exists:
            conn.execute(SCHEMA_V2)
            conn.execute("INSERT INTO schema_version (version) VALUES (%s)", (SCHEMA_VERSION,))
            return
        version = conn.execute("SELECT max(version) AS v FROM schema_version").fetchone()["v"]
    if version != SCHEMA_VERSION:
        raise MigrationError(
            f"Versão do esquema PostgreSQL desconhecida: {version} (esperada {SCHEMA_VERSION})."
        )
