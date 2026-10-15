from datetime import timezone

import psycopg

from ..domain.subscription import SubscriptionRequest

MAX_ERROR = 500


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
        return self._to_request(row, self._purposes([request_id]))

    def list_due(self, now, not_before, limit):
        rows = self.conn.execute(
            "SELECT * FROM subscription_requests WHERE delivery_status = 'pending' "
            "AND confirmed_at IS NULL AND next_attempt_at <= %s AND created_at >= %s "
            "ORDER BY id LIMIT %s",
            (utc(now), utc(not_before), limit),
        ).fetchall()
        purposes = self._purposes([r["id"] for r in rows])
        return [self._to_request(r, purposes) for r in rows]

    def claim(self, request_id, now) -> bool:
        with self.conn.transaction():
            cur = self.conn.execute(
                "UPDATE subscription_requests SET delivery_status = 'sending', "
                "attempts = attempts + 1, claimed_at = %s "
                "WHERE id = %s AND delivery_status = 'pending' AND confirmed_at IS NULL",
                (utc(now), request_id),
            )
        return cur.rowcount == 1

    def release_stale(self, before) -> int:
        with self.conn.transaction():
            cur = self.conn.execute(
                "UPDATE subscription_requests SET delivery_status = 'pending', claimed_at = NULL "
                "WHERE delivery_status = 'sending' AND claimed_at < %s",
                (utc(before),),
            )
        return cur.rowcount

    def release(self, request_id, undo_attempt=True):
        undo = ", attempts = CASE WHEN attempts > 0 THEN attempts - 1 ELSE 0 END" if undo_attempt else ""
        with self.conn.transaction():
            self.conn.execute(
                "UPDATE subscription_requests SET delivery_status = 'pending', claimed_at = NULL"
                + undo + " WHERE id = %s AND delivery_status = 'sending'",
                (request_id,),
            )

    def mark_sent(self, request_id, now):
        with self.conn.transaction():
            self.conn.execute(
                "UPDATE subscription_requests SET delivery_status = 'sent', sent_at = %s, "
                "claimed_at = NULL, last_error = '' WHERE id = %s",
                (utc(now), request_id),
            )

    def mark_failed(self, request_id, detail):
        with self.conn.transaction():
            self.conn.execute(
                "UPDATE subscription_requests SET delivery_status = 'failed', claimed_at = NULL, "
                "last_error = %s WHERE id = %s",
                (detail[:MAX_ERROR], request_id),
            )

    def mark_skipped(self, request_id, reason):
        with self.conn.transaction():
            self.conn.execute(
                "UPDATE subscription_requests SET delivery_status = 'skipped', claimed_at = NULL, "
                "skip_reason = %s WHERE id = %s",
                (reason, request_id),
            )

    def reschedule(self, request_id, retry_at, detail):
        with self.conn.transaction():
            self.conn.execute(
                "UPDATE subscription_requests SET delivery_status = 'pending', claimed_at = NULL, "
                "next_attempt_at = %s, last_error = %s WHERE id = %s",
                (utc(retry_at), detail[:MAX_ERROR], request_id),
            )

    def mark_confirmed(self, request_id, now) -> bool:
        with self.conn.transaction():
            cur = self.conn.execute(
                "UPDATE subscription_requests SET confirmed_at = %s "
                "WHERE id = %s AND confirmed_at IS NULL",
                (utc(now), request_id),
            )
        return cur.rowcount == 1

    def count_sent_since(self, since) -> int:
        return self.conn.execute(
            "SELECT count(*) AS n FROM subscription_requests "
            "WHERE delivery_status = 'sent' AND sent_at >= %s",
            (utc(since),),
        ).fetchone()["n"]

    def _purposes(self, ids) -> dict[int, tuple[str, ...]]:
        if not ids:
            return {}
        rows = self.conn.execute(
            "SELECT request_id, purpose FROM subscription_request_purposes "
            "WHERE request_id = ANY(%s) ORDER BY request_id, purpose",
            (list(ids),),
        ).fetchall()
        found: dict[int, list[str]] = {}
        for row in rows:
            found.setdefault(row["request_id"], []).append(row["purpose"])
        return {key: tuple(value) for key, value in found.items()}

    @staticmethod
    def _to_request(row, purposes) -> SubscriptionRequest:
        data = dict(row)
        data.pop("claimed_at")
        for key in ("created_at", "next_attempt_at", "sent_at", "confirmed_at"):
            value = data[key]
            data[key] = value.astimezone(timezone.utc).replace(tzinfo=None) if value else None
        return SubscriptionRequest(**data, purposes=purposes.get(data["id"], ()))
