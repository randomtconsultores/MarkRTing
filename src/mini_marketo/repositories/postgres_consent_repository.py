from datetime import timezone

import psycopg
from psycopg import errors

from ..domain.consent import ConsentRecord, ContactNotFoundError

_COLUMNS = "id, contact_id, purpose, decision, source, email, recorded_at"


class PostgresConsentRepository:
    """Mesmo contrato que ConsentRepository (SQLite), com SQL e erros do PostgreSQL."""

    def __init__(self, conn: psycopg.Connection):
        self.conn = conn

    def add(self, contact_id, purpose, decision, source, email) -> ConsentRecord:
        try:
            with self.conn.transaction():
                row = self.conn.execute(
                    "INSERT INTO consent_events (contact_id, purpose, decision, source, email) "
                    f"VALUES (%s, %s, %s, %s, %s) RETURNING {_COLUMNS}",
                    (contact_id, purpose, decision, source, email),
                ).fetchone()
        except errors.IntegrityError as exc:
            raise ContactNotFoundError("Contacto inexistente.") from exc
        return self._to_record(row)

    def list_for_contact(self, contact_id: int) -> list[ConsentRecord]:
        rows = self.conn.execute(
            f"SELECT {_COLUMNS} FROM consent_events WHERE contact_id = %s ORDER BY id",
            (contact_id,),
        ).fetchall()
        return [self._to_record(r) for r in rows]

    @staticmethod
    def _to_record(r) -> ConsentRecord:
        return ConsentRecord(
            id=r["id"], contact_id=r["contact_id"], purpose=r["purpose"],
            decision=r["decision"], source=r["source"], email=r["email"],
            recorded_at=r["recorded_at"].astimezone(timezone.utc).replace(tzinfo=None),
        )
