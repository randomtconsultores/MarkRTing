from datetime import timezone

import psycopg

from ..domain.suppression import Suppression

_COLUMNS = "id, email, reason, source, detail, created_at"

_UPSERT = (
    "INSERT INTO suppressions (email, reason, source, detail) VALUES (%s, %s, %s, %s) "
    "ON CONFLICT (email) DO UPDATE SET reason = excluded.reason, "
    "source = excluded.source, detail = excluded.detail "
    "WHERE suppressions.reason = 'manual' AND excluded.reason <> 'manual'"
)


class PostgresSuppressionRepository:
    """Mesmo contrato que SuppressionRepository (SQLite), com SQL do PostgreSQL."""

    def __init__(self, conn: psycopg.Connection):
        self.conn = conn

    def add(self, email, reason, source, detail) -> None:
        with self.conn.transaction():
            self.conn.execute(_UPSERT, (email, reason, source, detail))

    def get(self, email: str) -> Suppression | None:
        row = self.conn.execute(
            f"SELECT {_COLUMNS} FROM suppressions WHERE email = %s", (email,)
        ).fetchone()
        return self._to_record(row) if row else None

    def list_all(self) -> list[Suppression]:
        """Mais recente primeiro."""
        rows = self.conn.execute(
            f"SELECT {_COLUMNS} FROM suppressions ORDER BY id DESC"
        ).fetchall()
        return [self._to_record(r) for r in rows]

    def emails(self) -> set[str]:
        return {r["email"] for r in self.conn.execute("SELECT email FROM suppressions").fetchall()}

    def delete_manual(self, email: str) -> bool:
        with self.conn.transaction():
            cur = self.conn.execute(
                "DELETE FROM suppressions WHERE email = %s AND reason = 'manual'", (email,)
            )
        return cur.rowcount > 0

    @staticmethod
    def _to_record(r) -> Suppression:
        return Suppression(
            id=r["id"], email=r["email"], reason=r["reason"], source=r["source"],
            detail=r["detail"],
            created_at=r["created_at"].astimezone(timezone.utc).replace(tzinfo=None),
        )
