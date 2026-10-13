from datetime import timezone

import psycopg
from psycopg import errors

from ..domain.contact import ValidationError
from ..domain.segment import Segment
from .segment_json import criteria_from_json, criteria_to_json

_COLUMNS = "id, name, description, match_mode, criteria, created_at"


class PostgresSegmentRepository:
    """Mesmo contrato que SegmentRepository (SQLite), com SQL e erros do PostgreSQL."""

    def __init__(self, conn: psycopg.Connection):
        self.conn = conn

    def add(self, segment: Segment) -> Segment:
        try:
            with self.conn.transaction():
                row = self.conn.execute(
                    "INSERT INTO segments (name, description, match_mode, criteria) "
                    "VALUES (%s, %s, %s, %s) RETURNING id",
                    (
                        segment.name,
                        segment.description,
                        segment.match_mode,
                        criteria_to_json(segment.criteria),
                    ),
                ).fetchone()
        except errors.IntegrityError as exc:
            raise ValidationError("Já existe um segmento com esse nome.") from exc
        return self.get(row["id"])

    def update(self, segment: Segment) -> bool:
        """Atualiza nome, descrição, modo e critérios; id e created_at ficam como estavam."""
        try:
            with self.conn.transaction():
                cur = self.conn.execute(
                    "UPDATE segments SET name = %s, description = %s, match_mode = %s, "
                    "criteria = %s WHERE id = %s",
                    (
                        segment.name,
                        segment.description,
                        segment.match_mode,
                        criteria_to_json(segment.criteria),
                        segment.id,
                    ),
                )
        except errors.IntegrityError as exc:
            raise ValidationError("Já existe um segmento com esse nome.") from exc
        return cur.rowcount == 1

    def get(self, segment_id: int) -> Segment | None:
        row = self.conn.execute(
            f"SELECT {_COLUMNS} FROM segments WHERE id = %s", (segment_id,)
        ).fetchone()
        return self._to_segment(row) if row else None

    def list_all(self) -> list[Segment]:
        rows = self.conn.execute(
            f"SELECT {_COLUMNS} FROM segments ORDER BY lower(name), id"
        ).fetchall()
        return [self._to_segment(r) for r in rows]

    def delete(self, segment_id: int) -> bool:
        with self.conn.transaction():
            cur = self.conn.execute("DELETE FROM segments WHERE id = %s", (segment_id,))
        return cur.rowcount == 1

    @staticmethod
    def _to_segment(r) -> Segment:
        # Contrato comum aos dois backends: created_at é um datetime ingénuo em UTC.
        created_at = r["created_at"].astimezone(timezone.utc).replace(tzinfo=None)
        return Segment(
            id=r["id"],
            name=r["name"],
            description=r["description"],
            match_mode=r["match_mode"],
            criteria=criteria_from_json(r["criteria"]),
            created_at=created_at,
        )
