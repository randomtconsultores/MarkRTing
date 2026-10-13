from collections.abc import Iterable

from ..domain.contact import Contact, ValidationError
from ..domain.segment import Criterion, Segment


class SegmentService:
    """Segmentos dinamicos: os membros são calculados a partir dos contactos atuais.

    `segments` e `contacts` são repositórios do mesmo backend (e da mesma ligação).
    """

    def __init__(self, segments, contacts):
        self.segments = segments
        self.contacts = contacts

    def create_segment(
        self, name: str, description: str, match_mode: str, criteria: Iterable[Criterion]
    ) -> Segment:
        segment = Segment(None, name, description, match_mode, tuple(criteria))
        return self.segments.add(segment)

    def update_segment(
        self,
        segment_id: int,
        name: str,
        description: str,
        match_mode: str,
        criteria: Iterable[Criterion],
    ) -> Segment:
        # Valida tudo antes de gravar: um pedido inválido não altera nada.
        segment = Segment(segment_id, name, description, match_mode, tuple(criteria))
        if not self.segments.update(segment):
            raise ValidationError("O segmento não existe.")
        return self.get_segment(segment_id)

    def get_segment(self, segment_id: int) -> Segment:
        segment = self.segments.get(segment_id)
        if segment is None:
            raise ValidationError("O segmento não existe.")
        return segment

    def list_segments(self) -> list[Segment]:
        return self.segments.list_all()

    def delete_segment(self, segment_id: int) -> None:
        if not self.segments.delete(segment_id):
            raise ValidationError("O segmento não existe.")

    def members(self, segment_id: int) -> list[Contact]:
        segment = self.get_segment(segment_id)
        return segment.members(self.contacts.list_contacts())

    def list_with_counts(self) -> list[tuple[Segment, int]]:
        contacts = self.contacts.list_contacts()
        return [(s, len(s.members(contacts))) for s in self.segments.list_all()]
