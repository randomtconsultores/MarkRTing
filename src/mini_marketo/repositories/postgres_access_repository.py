import psycopg

from .access_repository import check_digest, session_record, user_record


class PostgresAccessRepository:
    """Persistencia de acesso; recebe hashes, nunca palavras-passe ou tokens em claro.

    Alterar credenciais, papel ou estado exige a versao observada e invalida
    todas as sessoes na mesma transacao. Nao implementa autorizacao do chamador.
    """

    def __init__(self, conn: psycopg.Connection):
        self.conn = conn

    def create_user(self, username, password_hash, role, now):
        with self.conn.transaction():
            cur = self.conn.execute(
                "INSERT INTO admin_users (username, password_hash, role, created_at) VALUES (%s, %s, %s, %s) RETURNING id",
                (username, password_hash, role, now),
            )
            user_id = cur.fetchone()["id"]
            return self.get_user(user_id)

    def get_user(self, user_id):
        return user_record(self.conn.execute("SELECT * FROM admin_users WHERE id = %s", (user_id,)).fetchone())

    def get_by_username(self, username):
        return user_record(self.conn.execute("SELECT * FROM admin_users WHERE username = %s", (username,)).fetchone())

    def update_user(self, user_id, expected_version, password_hash, role, is_active):
        if type(is_active) is not bool:
            raise ValueError("is_active tem de ser booleano.")
        with self.conn.transaction():
            cur = self.conn.execute(
                "UPDATE admin_users SET password_hash = %s, role = %s, is_active = %s, "
                "credential_version = credential_version + 1 WHERE id = %s AND credential_version = %s",
                (password_hash, role, int(is_active), user_id, expected_version),
            )
            if cur.rowcount != 1:
                return False
            self.conn.execute("DELETE FROM admin_sessions WHERE user_id = %s", (user_id,))
            return True

    def create_session(self, token_hash, user_id, credential_version, now, expires_at):
        check_digest(token_hash)
        with self.conn.transaction():
            cur = self.conn.execute(
                "INSERT INTO admin_sessions "
                "(token_hash, user_id, credential_version, created_at, last_seen_at, expires_at) "
                "SELECT %s, id, credential_version, %s, %s, %s FROM admin_users "
                "WHERE id = %s AND is_active = 1 AND credential_version = %s FOR SHARE",
                (token_hash, now, now, expires_at, user_id, credential_version),
            )
            return cur.rowcount == 1

    def get_valid_session(self, token_hash, now, idle_seconds):
        check_digest(token_hash)
        if type(idle_seconds) is not int or idle_seconds <= 0:
            raise ValueError("idle_seconds tem de ser um inteiro positivo.")
        return session_record(self.conn.execute(
            "SELECT s.* FROM admin_sessions s JOIN admin_users u ON u.id = s.user_id "
            "WHERE s.token_hash = %s AND u.is_active = 1 AND u.credential_version = s.credential_version "
            "AND s.expires_at > %s AND s.last_seen_at <= %s AND s.last_seen_at > %s",
            (token_hash, now, now, now - idle_seconds),
        ).fetchone())

    def revoke_session(self, token_hash):
        check_digest(token_hash)
        with self.conn.transaction():
            return self.conn.execute("DELETE FROM admin_sessions WHERE token_hash = %s", (token_hash,)).rowcount > 0

    def revoke_user_sessions(self, user_id):
        with self.conn.transaction():
            return self.conn.execute("DELETE FROM admin_sessions WHERE user_id = %s", (user_id,)).rowcount
