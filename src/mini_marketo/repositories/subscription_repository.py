import sqlite3
from datetime import datetime

from ..domain.subscription import SubscriptionRequest


def timestamp(moment):
    return moment.isoformat(sep=" ", timespec="microseconds")


class SubscriptionRepository:
    """Pedidos e finalidades na mesma transação. Datas em UTC ingénuo."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def add_limited(self, email, first_name, last_name, lang, purposes, now, since, limit):
        if self.conn.in_transaction:
            raise RuntimeError("O pedido exige uma ligação sem transação ativa.")
        try:
            self.conn.execute("BEGIN IMMEDIATE")
            count = self.conn.execute(
                "SELECT count(*) FROM subscription_requests WHERE email = ? AND created_at >= ?",
                (email, timestamp(since)),
            ).fetchone()[0]
            if count >= limit:
                self.conn.rollback()
                return None
            cur = self.conn.execute(
                "INSERT INTO subscription_requests "
                "(email, first_name, last_name, lang, created_at, next_attempt_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (email, first_name, last_name, lang, timestamp(now), timestamp(now)),
            )
            request_id = cur.lastrowid
            self.conn.executemany(
                "INSERT INTO subscription_request_purposes (request_id, purpose) VALUES (?, ?)",
                [(request_id, purpose) for purpose in purposes],
            )
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
        return request_id

    def get(self, request_id):
        row = self.conn.execute(
            "SELECT * FROM subscription_requests WHERE id = ?", (request_id,)
        ).fetchone()
        if row is None:
            return None
        purposes = tuple(r[0] for r in self.conn.execute(
            "SELECT purpose FROM subscription_request_purposes WHERE request_id = ? ORDER BY purpose",
            (request_id,),
        ))
        data = dict(row)
        for key in ("created_at", "next_attempt_at", "sent_at", "confirmed_at"):
            data[key] = datetime.fromisoformat(data[key]) if data[key] else None
        data.pop("claimed_at")
        return SubscriptionRequest(**data, purposes=purposes)
