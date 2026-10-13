import sqlite3
from datetime import datetime

from ..domain.contact import ValidationError
from ..domain.segment import Segment
from .segment_json import criteria_from_json, criteria_to_json

_COLUMNS = "id, name, description, match_mode, criteria, created_at"


class SegmentRepository:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def add(self, segment: Segment) -> Segment:
        try:
            with self.conn:
                cur = self.conn.execute(
                    "INSERT INTO segments (name, description, match_mode, criteria) "
                    "VALUES (?, ?, ?, ?)",
                    (
                        segment.name,
                        segment.description,
                        segment.match_mode,
                        criteria_to_json(segment.criteria),
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise ValidationError("Já existe um segmento com esse nome.") from exc
        return self.get(cur.lastrowid)

    def update(self, segment: Segment) -> bool:
        """Atualiza nome, descrição, modo e critérios; id e created_at ficam como estavam."""
        try:
            with self.conn:
                cur = self.conn.execute(
                    "UPDATE segments SET name = ?, description = ?, match_mode = ?, criteria = ? "
                    "WHERE id = ?",
                    (
                        segment.name,
                        segment.description,
                        segment.match_mode,
                        criteria_to_json(segment.criteria),
                        segment.id,
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise ValidationError("Já existe um segmento com esse nome.") from exc
        return cur.rowcount == 1

    def get(self, segment_id: int) -> Segment | None:
        row = self.conn.execute(
            f"SELECT {_COLUMNS} FROM segments WHERE id = ?", (segment_id,)
        ).fetchone()
        return self._to_segment(row) if row else None

    def list_all(self) -> list[Segment]:
        rows = self.conn.execute(
            f"SELECT {_COLUMNS} FROM segments ORDER BY lower(name), id"
        ).fetchall()
        return [self._to_segment(r) for r in rows]

    def delete(self, segment_id: int) -> bool:
        with self.conn:
            cur = self.conn.execute("DELETE FROM segments WHERE id = ?", (segment_id,))
        return cur.rowcount == 1

    @staticmethod
    def _to_segment(r) -> Segment:
        return Segment(
            id=r["id"],
            name=r["name"],
            description=r["description"],
            match_mode=r["match_mode"],
            criteria=criteria_from_json(r["criteria"]),
            created_at=datetime.fromisoformat(r["created_at"]),
        )
