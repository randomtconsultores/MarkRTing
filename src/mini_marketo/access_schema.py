"""DDL de acesso das migracoes v9 e v10, para SQLite e PostgreSQL.

Tempos são segundos Unix UTC, fornecidos pela aplicação. Segredos de sessão
não são persistidos: token_hash recebe o digest SHA-256 hexadecimal.
"""

_USER_COLUMNS = """
    username TEXT NOT NULL UNIQUE
        CHECK (length(username) BETWEEN 1 AND 128 AND username = lower(trim(username))),
    password_hash TEXT NOT NULL CHECK (length(trim(password_hash)) > 0),
    role TEXT NOT NULL CHECK (role IN ('admin', 'viewer')),
    is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    credential_version INTEGER NOT NULL DEFAULT 1 CHECK (credential_version >= 1),
    created_at BIGINT NOT NULL CHECK (created_at >= 0)
"""

_SESSION_DDL = """
CREATE TABLE admin_sessions (
    token_hash TEXT PRIMARY KEY NOT NULL CHECK (length(token_hash) = 64),
    user_id INTEGER NOT NULL REFERENCES admin_users (id) ON DELETE CASCADE,
    credential_version INTEGER NOT NULL CHECK (credential_version >= 1),
    created_at BIGINT NOT NULL CHECK (created_at >= 0),
    last_seen_at BIGINT NOT NULL CHECK (last_seen_at >= created_at),
    expires_at BIGINT NOT NULL CHECK (expires_at > created_at),
    CHECK (last_seen_at < expires_at)
);
CREATE INDEX ix_admin_sessions_user ON admin_sessions (user_id);
CREATE INDEX ix_admin_sessions_expiry ON admin_sessions (expires_at);
"""

SQLITE_ACCESS_V9 = (
    "CREATE TABLE admin_users (id INTEGER PRIMARY KEY AUTOINCREMENT,\n"
    + _USER_COLUMNS
    + ");\n"
    + _SESSION_DDL
)

POSTGRES_ACCESS_V9 = (
    "CREATE TABLE admin_users (id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,\n"
    + _USER_COLUMNS
    + ");\n"
    + _SESSION_DDL
)

SQLITE_LOGIN_ATTEMPTS_V10 = """
CREATE TABLE admin_login_attempts (
    bucket_hash TEXT PRIMARY KEY NOT NULL
        CHECK (length(bucket_hash) = 64 AND bucket_hash = lower(bucket_hash)
               AND bucket_hash NOT GLOB '*[^0-9a-f]*'),
    started_at INTEGER NOT NULL
        CHECK (typeof(started_at) = 'integer' AND started_at >= 0),
    expires_at INTEGER NOT NULL
        CHECK (typeof(expires_at) = 'integer' AND expires_at > started_at),
    attempts INTEGER NOT NULL
        CHECK (typeof(attempts) = 'integer' AND attempts > 0)
);
CREATE INDEX ix_admin_login_attempts_expiry ON admin_login_attempts (expires_at);
"""

POSTGRES_LOGIN_ATTEMPTS_V10 = """
CREATE TABLE admin_login_attempts (
    bucket_hash TEXT PRIMARY KEY NOT NULL CHECK (bucket_hash ~ '^[0-9a-f]{64}$'),
    started_at BIGINT NOT NULL CHECK (started_at >= 0),
    expires_at BIGINT NOT NULL CHECK (expires_at > started_at),
    attempts INTEGER NOT NULL CHECK (attempts > 0)
);
CREATE INDEX ix_admin_login_attempts_expiry ON admin_login_attempts (expires_at);
"""
