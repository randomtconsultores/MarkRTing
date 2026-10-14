import sqlite3
from datetime import datetime

from ..domain.consent import ConsentRecord, ContactNotFoundError

_COLUMNS = "id, contact_id, purpose, decision, source, email, recorded_at"


class ConsentRepository:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def add(self, contact_id, purpose, decision, source, email) -> ConsentRecord:
        try:
            with self.conn:
                cur = self.conn.execute(
                    "INSERT INTO consent_events (contact_id, purpose, decision, source, email) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (contact_id, purpose, decision, source, email),
                )
        except sqlite3.IntegrityError as exc:  # os restantes campos já foram validados
            raise ContactNotFoundError("Contacto inexistente.") from exc
        row = self.conn.execute(
            f"SELECT {_COLUMNS} FROM consent_events WHERE id = ?", (cur.lastrowid,)
        ).fetchone()
        return self._to_record(row)

    def list_for_contact(self, contact_id: int) -> list[ConsentRecord]:
        rows = self.conn.execute(
            f"SELECT {_COLUMNS} FROM consent_events WHERE contact_id = ? ORDER BY id",
            (contact_id,),
        ).fetchall()
        return [self._to_record(r) for r in rows]

    @staticmethod
    def _to_record(r) -> ConsentRecord:
        return ConsentRecord(
            id=r["id"], contact_id=r["contact_id"], purpose=r["purpose"],
            decision=r["decision"], source=r["source"], email=r["email"],
            recorded_at=datetime.fromisoformat(r["recorded_at"]),
        )
