from datetime import timezone

import psycopg

from ..domain.subscription import SubscriptionRequest


def utc(moment):
    return moment.replace(tzinfo=timezone.utc)


class PostgresSubscriptionRepository:
    """Mesmo contrato do repositório SQLite; a criação serializa pedidos do mesmo email."""

    def __init__(self, conn: psycopg.Connection):
        self.conn = conn

    def add_limited(self, email, first_name, last_name, lang, purposes, now, since, limit):
        with self.conn.transaction():
            # Chave estável por endereço; colisões apenas serializam endereços distintos.
            self.conn.execute(
                "SELECT pg_advisory_xact_lock(7305002, hashtext(%s))", (email,)
            )
            count = self.conn.execute(
                "SELECT count(*) AS n FROM subscription_requests WHERE email = %s AND created_at >= %s",
                (email, utc(since)),
            ).fetchone()["n"]
            if count >= limit:
                return None
            request_id = self.conn.execute(
                "INSERT INTO subscription_requests "
                "(email, first_name, last_name, lang, created_at, next_attempt_at) "
                "VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
                (email, first_name, last_name, lang, utc(now), utc(now)),
            ).fetchone()["id"]
            for purpose in purposes:
                self.conn.execute(
                    "INSERT INTO subscription_request_purposes (request_id, purpose) VALUES (%s, %s)",
                    (request_id, purpose),
                )
        return request_id

    def get(self, request_id):
        row = self.conn.execute(
            "SELECT * FROM subscription_requests WHERE id = %s", (request_id,)
        ).fetchone()
        if row is None:
            return None
        purposes = tuple(r["purpose"] for r in self.conn.execute(
            "SELECT purpose FROM subscription_request_purposes WHERE request_id = %s ORDER BY purpose",
            (request_id,),
        ).fetchall())
        data = dict(row)
        for key in ("created_at", "next_attempt_at", "sent_at", "confirmed_at"):
            data[key] = data[key].astimezone(timezone.utc).replace(tzinfo=None) if data[key] else None
        data.pop("claimed_at")
        return SubscriptionRequest(**data, purposes=purposes)
