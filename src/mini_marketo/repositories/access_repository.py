from dataclasses import dataclass, field
import sqlite3


@dataclass(frozen=True)
class AdminUser:
    id: int
    username: str
    password_hash: str = field(repr=False)
    role: str
    is_active: bool
    credential_version: int
    created_at: int


@dataclass(frozen=True)
class AdminSession:
    token_hash: str = field(repr=False)
    user_id: int
    credential_version: int
    created_at: int
    last_seen_at: int
    expires_at: int


def user_record(row):
    return AdminUser(**{**dict(row), "is_active": bool(row["is_active"])}) if row else None


def session_record(row):
    return AdminSession(**dict(row)) if row else None


def check_digest(value):
    if not isinstance(value, str) or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
        raise ValueError("Esperado um digest SHA-256 hexadecimal, nao um token de sessao.")


class AccessRepository:
    """Persistencia de acesso; recebe hashes, nunca palavras-passe ou tokens em claro.

    Alterar credenciais, papel ou estado exige a versao observada e invalida
    todas as sessoes na mesma transacao. Nao implementa autorizacao do chamador.
    """

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def create_user(self, username, password_hash, role, now):
        with self.conn:
            cur = self.conn.execute(
                "INSERT INTO admin_users (username, password_hash, role, created_at) VALUES (?, ?, ?, ?)",
                (username, password_hash, role, now),
            )
            user_id = cur.lastrowid
            return self.get_user(user_id)

    def get_user(self, user_id):
        return user_record(self.conn.execute("SELECT * FROM admin_users WHERE id = ?", (user_id,)).fetchone())

    def get_by_username(self, username):
        return user_record(self.conn.execute("SELECT * FROM admin_users WHERE username = ?", (username,)).fetchone())

    def update_user(self, user_id, expected_version, password_hash, role, is_active):
        if type(is_active) is not bool:
            raise ValueError("is_active tem de ser booleano.")
        with self.conn:
            cur = self.conn.execute(
                "UPDATE admin_users SET password_hash = ?, role = ?, is_active = ?, "
                "credential_version = credential_version + 1 WHERE id = ? AND credential_version = ?",
                (password_hash, role, int(is_active), user_id, expected_version),
            )
            if cur.rowcount != 1:
                return False
            self.conn.execute("DELETE FROM admin_sessions WHERE user_id = ?", (user_id,))
            return True

    def create_session(self, token_hash, user_id, credential_version, now, expires_at):
        check_digest(token_hash)
        with self.conn:
            cur = self.conn.execute(
                "INSERT INTO admin_sessions "
                "(token_hash, user_id, credential_version, created_at, last_seen_at, expires_at) "
                "SELECT ?, id, credential_version, ?, ?, ? FROM admin_users "
                "WHERE id = ? AND is_active = 1 AND credential_version = ?",
                (token_hash, now, now, expires_at, user_id, credential_version),
            )
            return cur.rowcount == 1

    def get_valid_session(self, token_hash, now, idle_seconds):
        check_digest(token_hash)
        if type(idle_seconds) is not int or idle_seconds <= 0:
            raise ValueError("idle_seconds tem de ser um inteiro positivo.")
        return session_record(self.conn.execute(
            "SELECT s.* FROM admin_sessions s JOIN admin_users u ON u.id = s.user_id "
            "WHERE s.token_hash = ? AND u.is_active = 1 AND u.credential_version = s.credential_version "
            "AND s.expires_at > ? AND s.last_seen_at <= ? AND s.last_seen_at > ?",
            (token_hash, now, now, now - idle_seconds),
        ).fetchone())

    def revoke_session(self, token_hash):
        check_digest(token_hash)
        with self.conn:
            return self.conn.execute("DELETE FROM admin_sessions WHERE token_hash = ?", (token_hash,)).rowcount > 0

    def revoke_user_sessions(self, user_id):
        with self.conn:
            return self.conn.execute("DELETE FROM admin_sessions WHERE user_id = ?", (user_id,)).rowcount
