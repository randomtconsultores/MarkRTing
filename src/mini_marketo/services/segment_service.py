from collections.abc import Iterable

from ..domain.contact import Contact, ValidationError
from ..domain.segment import Criterion, Segment


class SegmentService:
    """Segmentos dinamicos: os membros são calculados a partir dos contactos atuais.

    `segments`, `contacts` e `consents` são repositórios do mesmo backend (e da mesma
    ligação). `consents` só é necessário para segmentos com critérios de consentimento;
    sem ele, esses segmentos falham com um erro claro em vez de assumirem «sem registo».
    """

    def __init__(self, segments, contacts, consents=None):
        self.segments = segments
        self.contacts = contacts
        self.consents = consents

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

    def _records_by_contact(self, segments: Iterable[Segment]) -> dict:
        """Registos de consentimento por contacto; só são lidos se algum segmento os usar."""
        if not any(s.uses_consent for s in segments):
            return {}
        if self.consents is None:
            raise RuntimeError(
                "SegmentService sem repositório de consentimento: "
                "não consegue avaliar critérios de consentimento."
            )
        grouped: dict[int, list] = {}
        for record in self.consents.list_all():
            grouped.setdefault(record.contact_id, []).append(record)
        return grouped

    def members(self, segment_id: int) -> list[Contact]:
        segment = self.get_segment(segment_id)
        records = self._records_by_contact([segment])
        return segment.members(self.contacts.list_contacts(), records)

    def list_with_counts(self) -> list[tuple[Segment, int]]:
        contacts = self.contacts.list_contacts()
        segments = self.segments.list_all()
        records = self._records_by_contact(segments)
        return [(s, len(s.members(contacts, records))) for s in segments]
