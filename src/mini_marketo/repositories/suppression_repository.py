import sqlite3
from datetime import datetime

from ..domain.suppression import Suppression

_COLUMNS = "id, email, reason, source, detail, created_at"

# Idempotente. Uma entrada manual é promovida por um bounce ou uma queixa; nunca o contrário,
# e uma entrada não manual nunca é alterada (conserva a origem e a data originais).
_UPSERT = (
    "INSERT INTO suppressions (email, reason, source, detail) VALUES (?, ?, ?, ?) "
    "ON CONFLICT (email) DO UPDATE SET reason = excluded.reason, "
    "source = excluded.source, detail = excluded.detail "
    "WHERE suppressions.reason = 'manual' AND excluded.reason <> 'manual'"
)


class SuppressionRepository:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def add(self, email, reason, source, detail) -> None:
        with self.conn:
            self.conn.execute(_UPSERT, (email, reason, source, detail))

    def get(self, email: str) -> Suppression | None:
        row = self.conn.execute(
            f"SELECT {_COLUMNS} FROM suppressions WHERE email = ?", (email,)
        ).fetchone()
        return self._to_record(row) if row else None

    def list_all(self) -> list[Suppression]:
        """Mais recente primeiro."""
        rows = self.conn.execute(
            f"SELECT {_COLUMNS} FROM suppressions ORDER BY id DESC"
        ).fetchall()
        return [self._to_record(r) for r in rows]

    def emails(self) -> set[str]:
        return {r["email"] for r in self.conn.execute("SELECT email FROM suppressions")}

    def delete_manual(self, email: str) -> bool:
        """Só apaga se o motivo ainda for manual no momento da escrita."""
        with self.conn:
            cur = self.conn.execute(
                "DELETE FROM suppressions WHERE email = ? AND reason = 'manual'", (email,)
            )
        return cur.rowcount > 0

    @staticmethod
    def _to_record(r) -> Suppression:
        return Suppression(
            id=r["id"], email=r["email"], reason=r["reason"], source=r["source"],
            detail=r["detail"], created_at=datetime.fromisoformat(r["created_at"]),
        )
