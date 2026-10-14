from datetime import datetime, timezone

import psycopg
from psycopg import errors

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


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc)


def _naive(value: datetime | None) -> datetime | None:
    return value.astimezone(timezone.utc).replace(tzinfo=None) if value else None


class PostgresCampaignRepository:
    """Mesmo contrato que CampaignRepository (SQLite), com SQL e erros do PostgreSQL."""

    def __init__(self, conn: psycopg.Connection):
        self.conn = conn

    # --- campanhas ---

    def add_campaign(self, campaign: Campaign) -> Campaign:
        try:
            with self.conn.transaction():
                row = self.conn.execute(
                    "INSERT INTO campaigns "
                    "(name, subject, body_text, body_html, purpose, segment_id, segment_name) "
                    f"VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING {_CAMPAIGN_COLS}",
                    (
                        campaign.name,
                        campaign.subject,
                        campaign.body_text,
                        campaign.body_html,
                        campaign.purpose,
                        campaign.segment_id,
                        campaign.segment_name,
                    ),
                ).fetchone()
        except errors.UniqueViolation as exc:
            raise ValidationError("Já existe uma campanha com esse nome.") from exc
        except errors.ForeignKeyViolation as exc:
            raise ValidationError("O segmento da campanha não existe.") from exc
        return self._to_campaign(row)

    def get_campaign(self, campaign_id: int) -> Campaign | None:
        row = self.conn.execute(
            f"SELECT {_CAMPAIGN_COLS} FROM campaigns WHERE id = %s", (campaign_id,)
        ).fetchone()
        return self._to_campaign(row) if row else None

    def list_campaigns(self) -> list[Campaign]:
        rows = self.conn.execute(
            f"SELECT {_CAMPAIGN_COLS} FROM campaigns ORDER BY id DESC"
        ).fetchall()
        return [self._to_campaign(r) for r in rows]

    def queue_campaign(self, campaign_id, pending, skipped, now) -> None:
        """Passa o rascunho a enfileirada e cria as mensagens, tudo numa transação."""
        stamp = _aware(now)
        with self.conn.transaction():
            cur = self.conn.execute(
                "UPDATE campaigns SET status = 'queued', queued_at = %s "
                "WHERE id = %s AND status = 'draft'",
                (stamp, campaign_id),
            )
            if cur.rowcount != 1:
                raise ValidationError("A campanha não existe ou já foi enfileirada.")
            with self.conn.cursor() as cursor:
                cursor.executemany(
                    "INSERT INTO email_messages "
                    "(campaign_id, contact_id, email, status, next_attempt_at) "
                    "VALUES (%s, %s, %s, 'pending', %s) "
                    "ON CONFLICT (campaign_id, contact_id) DO NOTHING",
                    [(campaign_id, contact_id, email, stamp) for contact_id, email in pending],
                )
                cursor.executemany(
                    "INSERT INTO email_messages "
                    "(campaign_id, contact_id, email, status, next_attempt_at, skip_reason) "
                    "VALUES (%s, %s, %s, 'skipped', %s, %s) "
                    "ON CONFLICT (campaign_id, contact_id) DO NOTHING",
                    [
                        (campaign_id, contact_id, email, stamp, reason)
                        for contact_id, email, reason in skipped
                    ],
                )

    # --- consulta da caixa de saída ---

    def get_message(self, message_id: int) -> OutboxMessage | None:
        row = self.conn.execute(
            f"SELECT {_MESSAGE_COLS} FROM email_messages WHERE id = %s", (message_id,)
        ).fetchone()
        return self._to_message(row) if row else None

    def list_messages(self, campaign_id: int) -> list[OutboxMessage]:
        rows = self.conn.execute(
            f"SELECT {_MESSAGE_COLS} FROM email_messages WHERE campaign_id = %s ORDER BY id",
            (campaign_id,),
        ).fetchall()
        return [self._to_message(r) for r in rows]

    def problem_messages(self, campaign_id: int, limit: int = 50) -> list[OutboxMessage]:
        rows = self.conn.execute(
            f"SELECT {_MESSAGE_COLS} FROM email_messages "
            "WHERE campaign_id = %s AND status IN ('failed', 'skipped') ORDER BY id LIMIT %s",
            (campaign_id, limit),
        ).fetchall()
        return [self._to_message(r) for r in rows]

    def counts(self, campaign_id: int) -> dict[str, int]:
        rows = self.conn.execute(
            "SELECT status, count(*) AS n FROM email_messages WHERE campaign_id = %s GROUP BY status",
            (campaign_id,),
        ).fetchall()
        return {r["status"]: r["n"] for r in rows}

    def count_sent_since(self, since: datetime) -> int:
        row = self.conn.execute(
            "SELECT count(*) AS n FROM email_messages WHERE status = 'sent' AND sent_at >= %s",
            (_aware(since),),
        ).fetchone()
        return row["n"]

    # --- envio ---

    def claim_due(self, now: datetime, limit: int) -> list[OutboxMessage]:
        """Reclama até `limit` mensagens vencidas, de forma atómica e segura entre processos
        (FOR UPDATE SKIP LOCKED). Cada reclamação conta como uma tentativa."""
        stamp = _aware(now)
        with self.conn.transaction():
            rows = self.conn.execute(
                "UPDATE email_messages SET status = 'sending', claimed_at = %s, "
                "attempts = attempts + 1 WHERE id IN ("
                "SELECT id FROM email_messages WHERE status = 'pending' AND next_attempt_at <= %s "
                "ORDER BY id LIMIT %s FOR UPDATE SKIP LOCKED) "
                f"RETURNING {_MESSAGE_COLS}",
                (stamp, stamp, limit),
            ).fetchall()
        return sorted((self._to_message(r) for r in rows), key=lambda m: m.id)

    def mark_sent(self, message_id: int, now: datetime) -> None:
        with self.conn.transaction():
            self.conn.execute(
                "UPDATE email_messages SET status = 'sent', sent_at = %s, claimed_at = NULL, "
                "last_error = '' WHERE id = %s",
                (_aware(now), message_id),
            )

    def mark_failed(self, message_id: int, error: str) -> None:
        with self.conn.transaction():
            self.conn.execute(
                "UPDATE email_messages SET status = 'failed', claimed_at = NULL, last_error = %s "
                "WHERE id = %s",
                (error[:_MAX_ERROR], message_id),
            )

    def mark_skipped(self, message_id: int, reason: str) -> None:
        with self.conn.transaction():
            self.conn.execute(
                "UPDATE email_messages SET status = 'skipped', claimed_at = NULL, skip_reason = %s "
                "WHERE id = %s",
                (reason, message_id),
            )

    def reschedule(self, message_id: int, retry_at: datetime, error: str) -> None:
        with self.conn.transaction():
            self.conn.execute(
                "UPDATE email_messages SET status = 'pending', claimed_at = NULL, "
                "next_attempt_at = %s, last_error = %s WHERE id = %s",
                (_aware(retry_at), error[:_MAX_ERROR], message_id),
            )

    def release(self, message_ids: list[int], undo_attempt: bool) -> None:
        """Devolve mensagens reclamadas a pendentes; `undo_attempt` anula a tentativa contada."""
        if not message_ids:
            return
        attempts = _RELEASE_UNDO if undo_attempt else _RELEASE_KEEP
        with self.conn.transaction():
            self.conn.execute(
                "UPDATE email_messages SET status = 'pending', claimed_at = NULL, "
                f"attempts = {attempts} WHERE status = 'sending' AND id = ANY(%s)",
                (list(message_ids),),
            )

    def release_stale(self, before: datetime) -> int:
        """Mensagens reclamadas antes de `before` (processo que morreu) voltam a pendentes."""
        with self.conn.transaction():
            cur = self.conn.execute(
                "UPDATE email_messages SET status = 'pending', claimed_at = NULL "
                "WHERE status = 'sending' AND claimed_at < %s",
                (_aware(before),),
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
            created_at=_naive(r["created_at"]),
            queued_at=_naive(r["queued_at"]),
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
            next_attempt_at=_naive(r["next_attempt_at"]),
            last_error=r["last_error"],
            skip_reason=r["skip_reason"],
            sent_at=_naive(r["sent_at"]),
        )
