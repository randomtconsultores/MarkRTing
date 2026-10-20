import psycopg

from .access_repository import (
    LoginAttemptDecision,
    _LoginAttemptDenied,
    check_digest,
    session_record,
    user_record,
    validate_login_attempt_cleanup,
    validate_login_attempt_reservations,
)


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

    def touch_valid_session(self, token_hash, now, idle_seconds):
        """Refresh only a currently valid session, never resurrect or extend it."""
        check_digest(token_hash)
        if type(now) is not int or not 0 <= now <= 2**63 - 1:
            raise ValueError("now tem de ser um segundo Unix valido.")
        if type(idle_seconds) is not int or not 0 < idle_seconds <= 2**63 - 1:
            raise ValueError("idle_seconds tem de ser um inteiro positivo valido.")
        with self.conn.transaction():
            cur = self.conn.execute(
                "UPDATE admin_sessions SET last_seen_at = %s "
                "WHERE token_hash = %s AND expires_at > %s "
                "AND last_seen_at <= %s AND last_seen_at > %s "
                "AND EXISTS (SELECT 1 FROM admin_users u "
                "WHERE u.id = admin_sessions.user_id AND u.is_active = 1 "
                "AND u.credential_version = admin_sessions.credential_version)",
                (now, token_hash, now, now, now - idle_seconds),
            )
            if cur.rowcount != 1:
                return None
            return self.get_valid_session(token_hash, now, idle_seconds)

    def revoke_session(self, token_hash):
        check_digest(token_hash)
        with self.conn.transaction():
            return self.conn.execute("DELETE FROM admin_sessions WHERE token_hash = %s", (token_hash,)).rowcount > 0

    def revoke_user_sessions(self, user_id):
        with self.conn.transaction():
            return self.conn.execute("DELETE FROM admin_sessions WHERE user_id = %s", (user_id,)).rowcount

    def reserve_login_attempts(self, buckets, now, window_seconds):
        """Reserve all supplied budgets atomically or leave every counter unchanged."""
        buckets, expires_at = validate_login_attempt_reservations(
            buckets, now, window_seconds
        )
        try:
            with self.conn.transaction():
                retry_afters = []
                for bucket_hash, limit in buckets:
                    row = self.conn.execute(
                        "INSERT INTO admin_login_attempts "
                        "(bucket_hash, started_at, expires_at, attempts) VALUES (%s, %s, %s, 1) "
                        "ON CONFLICT(bucket_hash) DO UPDATE SET "
                        "started_at = CASE "
                        "WHEN admin_login_attempts.expires_at <= EXCLUDED.started_at "
                        "THEN EXCLUDED.started_at ELSE admin_login_attempts.started_at END, "
                        "expires_at = CASE "
                        "WHEN admin_login_attempts.expires_at <= EXCLUDED.started_at "
                        "THEN EXCLUDED.expires_at ELSE admin_login_attempts.expires_at END, "
                        "attempts = CASE "
                        "WHEN admin_login_attempts.expires_at <= EXCLUDED.started_at "
                        "THEN 1 ELSE admin_login_attempts.attempts + 1 END "
                        "WHERE admin_login_attempts.expires_at <= EXCLUDED.started_at "
                        "OR (admin_login_attempts.started_at <= EXCLUDED.started_at "
                        "AND admin_login_attempts.attempts < %s) "
                        "RETURNING attempts",
                        (bucket_hash, now, expires_at, limit),
                    ).fetchone()
                    if row is None:
                        stored = self.conn.execute(
                            "SELECT expires_at FROM admin_login_attempts WHERE bucket_hash = %s",
                            (bucket_hash,),
                        ).fetchone()
                        if stored is None:
                            raise RuntimeError("O limite de tentativas desapareceu durante a reserva.")
                        retry_afters.append(max(1, stored["expires_at"] - now))
                if retry_afters:
                    raise _LoginAttemptDenied(max(retry_afters))
        except _LoginAttemptDenied as denied:
            return LoginAttemptDecision(False, denied.retry_after)
        return LoginAttemptDecision(True, 0)

    def cleanup_expired_login_attempts(self, now, *, batch_size=1000):
        """Remove at most one batch; recheck expiry before deleting refreshed rows."""
        validate_login_attempt_cleanup(now, batch_size)
        with self.conn.transaction():
            return self.conn.execute(
                "DELETE FROM admin_login_attempts WHERE bucket_hash IN ("
                "SELECT bucket_hash FROM admin_login_attempts WHERE expires_at <= %s "
                "ORDER BY expires_at, bucket_hash LIMIT %s) AND expires_at <= %s",
                (now, batch_size, now),
            ).rowcount

    def cleanup_expired_sessions(self, now, *, batch_size=1000):
        """Remove at most one batch of expired sessions."""
        validate_login_attempt_cleanup(now, batch_size)
        with self.conn.transaction():
            return self.conn.execute(
                "DELETE FROM admin_sessions WHERE token_hash IN ("
                "SELECT token_hash FROM admin_sessions WHERE expires_at <= %s "
                "ORDER BY expires_at, token_hash LIMIT %s) AND expires_at <= %s",
                (now, batch_size, now),
            ).rowcount
