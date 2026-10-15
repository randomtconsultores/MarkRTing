import sqlite3
from datetime import datetime

from ..domain.subscription import SubscriptionRequest

MAX_ERROR = 500


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
        return self._to_request(row, self._purposes([request_id]))

    def list_due(self, now, not_before, limit):
        """Pendentes por enviar, ainda dentro da validade, não confirmados, do mais antigo."""
        rows = self.conn.execute(
            "SELECT * FROM subscription_requests WHERE delivery_status = 'pending' "
            "AND confirmed_at IS NULL AND next_attempt_at <= ? AND created_at >= ? "
            "ORDER BY id LIMIT ?",
            (timestamp(now), timestamp(not_before), limit),
        ).fetchall()
        purposes = self._purposes([r["id"] for r in rows])
        return [self._to_request(r, purposes) for r in rows]

    def claim(self, request_id, now) -> bool:
        """Reclama o pedido para envio; conta como uma tentativa. Só um processo o consegue."""
        with self.conn:
            cur = self.conn.execute(
                "UPDATE subscription_requests SET delivery_status = 'sending', "
                "attempts = attempts + 1, claimed_at = ? "
                "WHERE id = ? AND delivery_status = 'pending' AND confirmed_at IS NULL",
                (timestamp(now), request_id),
            )
        return cur.rowcount == 1

    def release_stale(self, before) -> int:
        with self.conn:
            cur = self.conn.execute(
                "UPDATE subscription_requests SET delivery_status = 'pending', claimed_at = NULL "
                "WHERE delivery_status = 'sending' AND claimed_at < ?",
                (timestamp(before),),
            )
        return cur.rowcount

    def release(self, request_id, undo_attempt=True):
        undo = ", attempts = CASE WHEN attempts > 0 THEN attempts - 1 ELSE 0 END" if undo_attempt else ""
        with self.conn:
            self.conn.execute(
                "UPDATE subscription_requests SET delivery_status = 'pending', claimed_at = NULL"
                + undo + " WHERE id = ? AND delivery_status = 'sending'",
                (request_id,),
            )

    def mark_sent(self, request_id, now):
        with self.conn:
            self.conn.execute(
                "UPDATE subscription_requests SET delivery_status = 'sent', sent_at = ?, "
                "claimed_at = NULL, last_error = '' WHERE id = ?",
                (timestamp(now), request_id),
            )

    def mark_failed(self, request_id, detail):
        with self.conn:
            self.conn.execute(
                "UPDATE subscription_requests SET delivery_status = 'failed', claimed_at = NULL, "
                "last_error = ? WHERE id = ?",
                (detail[:MAX_ERROR], request_id),
            )

    def mark_skipped(self, request_id, reason):
        with self.conn:
            self.conn.execute(
                "UPDATE subscription_requests SET delivery_status = 'skipped', claimed_at = NULL, "
                "skip_reason = ? WHERE id = ?",
                (reason, request_id),
            )

    def reschedule(self, request_id, retry_at, detail):
        with self.conn:
            self.conn.execute(
                "UPDATE subscription_requests SET delivery_status = 'pending', claimed_at = NULL, "
                "next_attempt_at = ?, last_error = ? WHERE id = ?",
                (timestamp(retry_at), detail[:MAX_ERROR], request_id),
            )

    def mark_confirmed(self, request_id, now) -> bool:
        """True só na primeira confirmação; as seguintes não alteram nada."""
        with self.conn:
            cur = self.conn.execute(
                "UPDATE subscription_requests SET confirmed_at = ? "
                "WHERE id = ? AND confirmed_at IS NULL",
                (timestamp(now), request_id),
            )
        return cur.rowcount == 1

    def count_sent_since(self, since) -> int:
        return self.conn.execute(
            "SELECT count(*) FROM subscription_requests "
            "WHERE delivery_status = 'sent' AND sent_at >= ?",
            (timestamp(since),),
        ).fetchone()[0]

    def _purposes(self, ids) -> dict[int, tuple[str, ...]]:
        if not ids:
            return {}
        marks = ", ".join("?" for _ in ids)
        rows = self.conn.execute(
            "SELECT request_id, purpose FROM subscription_request_purposes "
            f"WHERE request_id IN ({marks}) ORDER BY request_id, purpose",
            tuple(ids),
        ).fetchall()
        found: dict[int, list[str]] = {}
        for row in rows:
            found.setdefault(row[0], []).append(row[1])
        return {key: tuple(value) for key, value in found.items()}

    @staticmethod
    def _to_request(row, purposes) -> SubscriptionRequest:
        data = dict(row)
        data.pop("claimed_at")
        for key in ("created_at", "next_attempt_at", "sent_at", "confirmed_at"):
            data[key] = datetime.fromisoformat(data[key]) if data[key] else None
        return SubscriptionRequest(**data, purposes=purposes.get(data["id"], ()))
