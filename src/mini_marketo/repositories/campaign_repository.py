import sqlite3
from datetime import datetime

from ..domain.campaign import Campaign, OutboxMessage
from ..domain.contact import ValidationError

_CAMPAIGN_COLS = (
    "id, name, subject, body_text, body_html, purpose, segment_id, segment_name, "
    "status, created_at, queued_at"
)
_MESSAGE_COLS = (
    "id, campaign_id, contact_id, email, status, attempts, next_attempt_at, "
    "last_error, skip_reason, sent_at"
)
_MAX_ERROR = 500
_RELEASE_KEEP = "attempts"
_RELEASE_UNDO = "CASE WHEN attempts > 0 THEN attempts - 1 ELSE 0 END"


def _ts(value: datetime) -> str:
    return value.strftime("%Y-%m-%d %H:%M:%S")


def _parse(value) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


class CampaignRepository:
    """Campanhas e caixa de saída (SQLite). Datas: UTC ingénuo, como o resto do projeto."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    # --- campanhas ---

    def add_campaign(self, campaign: Campaign) -> Campaign:
        try:
            with self.conn:
                cur = self.conn.execute(
                    "INSERT INTO campaigns "
                    "(name, subject, body_text, body_html, purpose, segment_id, segment_name) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        campaign.name,
                        campaign.subject,
                        campaign.body_text,
                        campaign.body_html,
                        campaign.purpose,
                        campaign.segment_id,
                        campaign.segment_name,
                    ),
                )
        except sqlite3.IntegrityError as exc:
            if "UNIQUE" in str(exc):
                raise ValidationError("Já existe uma campanha com esse nome.") from exc
            raise ValidationError("O segmento da campanha não existe.") from exc
        return self.get_campaign(cur.lastrowid)

    def get_campaign(self, campaign_id: int) -> Campaign | None:
        row = self.conn.execute(
            f"SELECT {_CAMPAIGN_COLS} FROM campaigns WHERE id = ?", (campaign_id,)
        ).fetchone()
        return self._to_campaign(row) if row else None

    def list_campaigns(self) -> list[Campaign]:
        rows = self.conn.execute(
            f"SELECT {_CAMPAIGN_COLS} FROM campaigns ORDER BY id DESC"
        ).fetchall()
        return [self._to_campaign(r) for r in rows]

    def queue_campaign(self, campaign_id, pending, skipped, now) -> None:
        """Passa o rascunho a enfileirada e cria as mensagens, tudo numa transação.

        `pending`: (contact_id, email); `skipped`: (contact_id, email, motivo). Uma campanha
        que não seja rascunho não altera nada.
        """
        stamp = _ts(now)
        with self.conn:
            cur = self.conn.execute(
                "UPDATE campaigns SET status = 'queued', queued_at = ? "
                "WHERE id = ? AND status = 'draft'",
                (stamp, campaign_id),
            )
            if cur.rowcount != 1:
                raise ValidationError("A campanha não existe ou já foi enfileirada.")
            self.conn.executemany(
                "INSERT INTO email_messages (campaign_id, contact_id, email, status, next_attempt_at) "
                "VALUES (?, ?, ?, 'pending', ?) ON CONFLICT (campaign_id, contact_id) DO NOTHING",
                [(campaign_id, contact_id, email, stamp) for contact_id, email in pending],
            )
            self.conn.executemany(
                "INSERT INTO email_messages "
                "(campaign_id, contact_id, email, status, next_attempt_at, skip_reason) "
                "VALUES (?, ?, ?, 'skipped', ?, ?) ON CONFLICT (campaign_id, contact_id) DO NOTHING",
                [(campaign_id, contact_id, email, stamp, reason) for contact_id, email, reason in skipped],
            )

    # --- consulta da caixa de saída ---

    def get_message(self, message_id: int) -> OutboxMessage | None:
        row = self.conn.execute(
            f"SELECT {_MESSAGE_COLS} FROM email_messages WHERE id = ?", (message_id,)
        ).fetchone()
        return self._to_message(row) if row else None

    def list_messages(self, campaign_id: int) -> list[OutboxMessage]:
        rows = self.conn.execute(
            f"SELECT {_MESSAGE_COLS} FROM email_messages WHERE campaign_id = ? ORDER BY id",
            (campaign_id,),
        ).fetchall()
        return [self._to_message(r) for r in rows]

    def problem_messages(self, campaign_id: int, limit: int = 50) -> list[OutboxMessage]:
        rows = self.conn.execute(
            f"SELECT {_MESSAGE_COLS} FROM email_messages "
            "WHERE campaign_id = ? AND status IN ('failed', 'skipped') ORDER BY id LIMIT ?",
            (campaign_id, limit),
        ).fetchall()
        return [self._to_message(r) for r in rows]

    def counts(self, campaign_id: int) -> dict[str, int]:
        rows = self.conn.execute(
            "SELECT status, count(*) AS n FROM email_messages WHERE campaign_id = ? GROUP BY status",
            (campaign_id,),
        ).fetchall()
        return {r["status"]: r["n"] for r in rows}

    def count_sent_since(self, since: datetime) -> int:
        row = self.conn.execute(
            "SELECT count(*) AS n FROM email_messages WHERE status = 'sent' AND sent_at >= ?",
            (_ts(since),),
        ).fetchone()
        return row["n"]

    # --- envio ---

    def claim_due(self, now: datetime, limit: int) -> list[OutboxMessage]:
        """Reclama até `limit` mensagens vencidas. A reclamação de cada uma é atómica (o
        UPDATE só acerta se ainda estiver pendente) e conta como uma tentativa."""
        stamp = _ts(now)
        ids = [
            r["id"]
            for r in self.conn.execute(
                "SELECT id FROM email_messages WHERE status = 'pending' AND next_attempt_at <= ? "
                "ORDER BY id LIMIT ?",
                (stamp, limit),
            ).fetchall()
        ]
        claimed = []
        for message_id in ids:
            with self.conn:
                cur = self.conn.execute(
                    "UPDATE email_messages SET status = 'sending', claimed_at = ?, "
                    "attempts = attempts + 1 WHERE id = ? AND status = 'pending'",
                    (stamp, message_id),
                )
            if cur.rowcount == 1:
                claimed.append(self.get_message(message_id))
        return claimed

    def mark_sent(self, message_id: int, now: datetime) -> None:
        with self.conn:
            self.conn.execute(
                "UPDATE email_messages SET status = 'sent', sent_at = ?, claimed_at = NULL, "
                "last_error = '' WHERE id = ?",
                (_ts(now), message_id),
            )

    def mark_failed(self, message_id: int, error: str) -> None:
        with self.conn:
            self.conn.execute(
                "UPDATE email_messages SET status = 'failed', claimed_at = NULL, last_error = ? "
                "WHERE id = ?",
                (error[:_MAX_ERROR], message_id),
            )

    def mark_skipped(self, message_id: int, reason: str) -> None:
        with self.conn:
            self.conn.execute(
                "UPDATE email_messages SET status = 'skipped', claimed_at = NULL, skip_reason = ? "
                "WHERE id = ?",
                (reason, message_id),
            )

    def reschedule(self, message_id: int, retry_at: datetime, error: str) -> None:
        with self.conn:
            self.conn.execute(
                "UPDATE email_messages SET status = 'pending', claimed_at = NULL, "
                "next_attempt_at = ?, last_error = ? WHERE id = ?",
                (_ts(retry_at), error[:_MAX_ERROR], message_id),
            )

    def release(self, message_ids: list[int], undo_attempt: bool) -> None:
        """Devolve mensagens reclamadas a pendentes; `undo_attempt` anula a tentativa contada."""
        if not message_ids:
            return
        attempts = _RELEASE_UNDO if undo_attempt else _RELEASE_KEEP
        marks = ",".join("?" * len(message_ids))
        with self.conn:
            self.conn.execute(
                f"UPDATE email_messages SET status = 'pending', claimed_at = NULL, "
                f"attempts = {attempts} WHERE status = 'sending' AND id IN ({marks})",
                list(message_ids),
            )

    def release_stale(self, before: datetime) -> int:
        """Mensagens reclamadas antes de `before` (processo que morreu) voltam a pendentes."""
        with self.conn:
            cur = self.conn.execute(
                "UPDATE email_messages SET status = 'pending', claimed_at = NULL "
                "WHERE status = 'sending' AND claimed_at < ?",
                (_ts(before),),
            )
        return cur.rowcount

    @staticmethod
    def _to_campaign(r) -> Campaign:
        return Campaign(
            id=r["id"],
            name=r["name"],
            subject=r["subject"],
            body_text=r["body_text"],
            body_html=r["body_html"],
            purpose=r["purpose"],
            segment_id=r["segment_id"],
            segment_name=r["segment_name"],
            status=r["status"],
            created_at=_parse(r["created_at"]),
            queued_at=_parse(r["queued_at"]),
        )

    @staticmethod
    def _to_message(r) -> OutboxMessage:
        return OutboxMessage(
            id=r["id"],
            campaign_id=r["campaign_id"],
            contact_id=r["contact_id"],
            email=r["email"],
            status=r["status"],
            attempts=r["attempts"],
            next_attempt_at=_parse(r["next_attempt_at"]),
            last_error=r["last_error"],
            skip_reason=r["skip_reason"],
            sent_at=_parse(r["sent_at"]),
        )
