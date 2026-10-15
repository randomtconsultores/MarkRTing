import psycopg
from psycopg.rows import dict_row

from .migrations import Migration, MigrationError, latest_version, pending

BASELINE_VERSION = 2

SEGMENTS_V3 = """
CREATE TABLE segments (
    id          INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name        TEXT NOT NULL CHECK (length(trim(name)) > 0),
    description TEXT NOT NULL DEFAULT '',
    match_mode  TEXT NOT NULL CHECK (match_mode IN ('all', 'any')),
    criteria    TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX ux_segments_name ON segments (lower(name));
"""

CONSENT_V4 = """
CREATE TABLE consent_events (
    id          INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    contact_id  INTEGER NOT NULL REFERENCES contacts (id) ON DELETE RESTRICT,
    purpose     TEXT NOT NULL CHECK (purpose IN ('newsletter', 'promotional')),
    decision    TEXT NOT NULL CHECK (decision IN ('granted', 'withdrawn')),
    source      TEXT NOT NULL CHECK (source IN ('form', 'admin')),
    email       TEXT NOT NULL CHECK (length(email) > 0 AND email = lower(trim(email))),
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ix_consent_contact_purpose ON consent_events (contact_id, purpose, id);
"""

CAMPAIGNS_V5 = """
CREATE TABLE campaigns (
    id           INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name         TEXT NOT NULL CHECK (length(trim(name)) > 0),
    subject      TEXT NOT NULL CHECK (length(trim(subject)) > 0),
    body_text    TEXT NOT NULL CHECK (length(trim(body_text)) > 0),
    body_html    TEXT NOT NULL DEFAULT '',
    purpose      TEXT NOT NULL CHECK (purpose IN ('newsletter', 'promotional')),
    segment_id   INTEGER REFERENCES segments (id) ON DELETE SET NULL,
    segment_name TEXT NOT NULL CHECK (length(trim(segment_name)) > 0),
    status       TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'queued')),
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    queued_at    TIMESTAMPTZ
);
CREATE UNIQUE INDEX ux_campaigns_name ON campaigns (lower(name));

CREATE TABLE email_messages (
    id              INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    campaign_id     INTEGER NOT NULL REFERENCES campaigns (id) ON DELETE RESTRICT,
    contact_id      INTEGER NOT NULL REFERENCES contacts (id) ON DELETE RESTRICT,
    email           TEXT NOT NULL CHECK (length(email) > 0 AND email = lower(trim(email))),
    status          TEXT NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending', 'sending', 'sent', 'failed', 'skipped')),
    attempts        INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    claimed_at      TIMESTAMPTZ,
    last_error      TEXT NOT NULL DEFAULT '',
    skip_reason     TEXT NOT NULL DEFAULT '',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    sent_at         TIMESTAMPTZ,
    CONSTRAINT email_messages_campaign_contact_key UNIQUE (campaign_id, contact_id)
);
CREATE INDEX ix_email_messages_due ON email_messages (status, next_attempt_at);
CREATE INDEX ix_email_messages_campaign ON email_messages (campaign_id, status);
"""

# No PostgreSQL basta trocar a restrição. O nome é o que o próprio PostgreSQL deu à restrição
# da coluna em CONSENT_V4; se não existir, a migração falha em vez de deixar a antiga ativa.
CONSENT_SOURCE_V6 = """
ALTER TABLE consent_events DROP CONSTRAINT consent_events_source_check;
ALTER TABLE consent_events ADD CONSTRAINT consent_events_source_check
    CHECK (source IN ('form', 'admin', 'unsubscribe'));
"""

# Lista de supressão por endereço (não por contacto: um bounce é sobre o endereço, que pode
# deixar de estar associado a qualquer contacto). Só cria objetos novos; não toca nos dados.
SUPPRESSIONS_V7 = """
CREATE TABLE suppressions (
    id         INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    email      TEXT NOT NULL CHECK (length(email) > 0 AND email = lower(trim(email))),
    reason     TEXT NOT NULL CHECK (reason IN ('hard_bounce', 'complaint', 'manual')),
    source     TEXT NOT NULL CHECK (source IN ('smtp', 'event', 'admin')),
    detail     TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK ((reason = 'manual') = (source = 'admin'))
);
CREATE UNIQUE INDEX ux_suppressions_email ON suppressions (email);
"""

# Esquema criado de raiz na versão-base; as versões seguintes entram em MIGRATIONS (SQL do PostgreSQL).
MIGRATIONS: list[Migration] = [
    (3, SEGMENTS_V3),
    (4, CONSENT_V4),
    (5, CAMPAIGNS_V5),
    (6, CONSENT_SOURCE_V6),
    (7, SUPPRESSIONS_V7),
]
SCHEMA_VERSION = latest_version(BASELINE_VERSION, MIGRATIONS)

_LOCK_ID = 7305001

BASELINE_DDL = """
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
    """Cria o esquema-base ou aplica as migrações pendentes, tudo numa única transação."""
    latest = latest_version(BASELINE_VERSION, MIGRATIONS)
    with conn.transaction():
        conn.execute("SELECT pg_advisory_xact_lock(%s)", (_LOCK_ID,))
        exists = conn.execute(
            "SELECT to_regclass('schema_version') IS NOT NULL AS ok"
        ).fetchone()["ok"]
        if exists:
            version = conn.execute("SELECT max(version) AS v FROM schema_version").fetchone()["v"]
        else:
            conn.execute(BASELINE_DDL)
            conn.execute("INSERT INTO schema_version (version) VALUES (%s)", (BASELINE_VERSION,))
            version = BASELINE_VERSION
        if version > latest:
            raise MigrationError(
                f"A base de dados (versão {version}) é mais recente do que a aplicação (versão {latest})."
            )
        if version < BASELINE_VERSION:
            raise MigrationError(f"Versão do esquema desconhecida: {version}.")
        for target, script in pending(version, MIGRATIONS):
            conn.execute(script)
            conn.execute("UPDATE schema_version SET version = %s", (target,))
