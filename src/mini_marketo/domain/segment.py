import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime

from .consent import PURPOSES, STATUS_LABELS, ConsentRecord, current_status
from .contact import Contact, ValidationError, clean_name

MAX_VALUE_LENGTH = 200
MAX_DESCRIPTION_LENGTH = 500
MAX_CRITERIA = 20
MATCH_MODES = ("all", "any")

CONSENT_FIELD_PREFIX = "consent_"

_TEXT_OPERATORS = ("is", "is_not", "contains", "starts_with")
_VALUELESS_OPERATORS = ("is_empty", "is_not_empty")

OPERATORS_BY_FIELD = {
    "first_name": _TEXT_OPERATORS,
    "last_name": _TEXT_OPERATORS,
    "email": _TEXT_OPERATORS,
    "company": _TEXT_OPERATORS + _VALUELESS_OPERATORS,
    "created_at": ("before", "after"),
    **{f"{CONSENT_FIELD_PREFIX}{purpose}": ("is", "is_not") for purpose in PURPOSES},
}

# Estados de consentimento aceites: a chave interna ou o termo em português.
_CONSENT_VALUES = {key: key for key in STATUS_LABELS} | {
    label.casefold(): key for key, label in STATUS_LABELS.items()
}

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_DATA_KEYS = {"field", "operator", "value"}


@dataclass(frozen=True)
class Criterion:
    """Uma condição sobre um contacto. Quando existe, é sempre válida.

    Texto: comparação sem distinguir maiúsculas (os acentos contam como diferença).
    Email: os operadores positivos cumprem-se se QUALQUER email cumprir; `is_not` só se
    NENHUM email for igual. Datas: compara-se o dia em UTC (formato AAAA-MM-DD).
    Consentimento (`consent_<finalidade>`): compara o estado atual derivado do histórico
    (concedido, retirado ou sem registo); o valor guarda-se como chave interna.
    """

    field: str
    operator: str
    value: str | None = None

    def __post_init__(self):
        operators = OPERATORS_BY_FIELD.get(self.field)
        if operators is None:
            raise ValidationError(f"Campo de critério desconhecido: '{self.field}'.")
        if self.operator not in operators:
            raise ValidationError(
                f"O operador '{self.operator}' não se aplica ao campo '{self.field}'."
            )
        object.__setattr__(self, "value", self._clean_value())

    @property
    def is_consent(self) -> bool:
        return self.field.startswith(CONSENT_FIELD_PREFIX)

    @property
    def display_value(self) -> str | None:
        """Valor para mostrar ao utilizador (o estado de consentimento em português)."""
        if self.value is not None and self.is_consent:
            return STATUS_LABELS[self.value]
        return self.value

    def _clean_value(self) -> str | None:
        raw = (self.value or "").strip()
        if self.operator in _VALUELESS_OPERATORS:
            if raw:
                raise ValidationError(f"O operador '{self.operator}' não aceita valor.")
            return None
        if not raw:
            raise ValidationError("O critério precisa de um valor.")
        if len(raw) > MAX_VALUE_LENGTH:
            raise ValidationError(f"O valor do critério excede {MAX_VALUE_LENGTH} caracteres.")
        if self.is_consent:
            state = _CONSENT_VALUES.get(" ".join(raw.casefold().split()))
            if state is None:
                raise ValidationError(
                    "Estado de consentimento inválido: use concedido, retirado ou sem registo."
                )
            return state
        if self.field == "created_at":
            if not _DATE_RE.match(raw):
                raise ValidationError("Data inválida: use o formato AAAA-MM-DD.")
            try:
                return date.fromisoformat(raw).isoformat()
            except ValueError as exc:
                raise ValidationError("Data inválida: use o formato AAAA-MM-DD.") from exc
        return raw

    def matches(self, contact: Contact, records: Iterable[ConsentRecord] = ()) -> bool:
        """`records` são os registos de consentimento do contacto; só contam nos critérios
        de consentimento (sem registos, o estado é «sem registo»)."""
        if self.is_consent:
            purpose = self.field[len(CONSENT_FIELD_PREFIX):]
            return (current_status(records, purpose) == self.value) == (self.operator == "is")
        if self.field == "created_at":
            return self._matches_date(contact.created_at)
        candidates = self._candidates(contact)
        if self.operator == "is_empty":
            return not candidates
        if self.operator == "is_not_empty":
            return bool(candidates)
        wanted = self.value.casefold()
        if self.operator == "is":
            return any(c == wanted for c in candidates)
        if self.operator == "is_not":
            return not any(c == wanted for c in candidates)
        if self.operator == "contains":
            return any(wanted in c for c in candidates)
        return any(c.startswith(wanted) for c in candidates)  # starts_with

    def _candidates(self, contact: Contact) -> list[str]:
        if self.field == "email":
            raw = list(contact.emails)
        elif self.field == "company":
            raw = [contact.company_name] if contact.company_name else []
        else:
            raw = [getattr(contact, self.field)]
        return [r.casefold() for r in raw]

    def _matches_date(self, created_at: datetime | None) -> bool:
        if created_at is None:
            return False
        target = date.fromisoformat(self.value)
        day = created_at.date()
        return day < target if self.operator == "before" else day > target

    def to_data(self) -> dict:
        return {"field": self.field, "operator": self.operator, "value": self.value}

    @classmethod
    def from_data(cls, data) -> "Criterion":
        if (
            not isinstance(data, dict)
            or not {"field", "operator"} <= set(data)
            or set(data) - _DATA_KEYS
        ):
            raise ValidationError("Critério mal formado.")
        value = data.get("value")
        if (
            not isinstance(data["field"], str)
            or not isinstance(data["operator"], str)
            or (value is not None and not isinstance(value, str))
        ):
            raise ValidationError("Critério mal formado.")
        return cls(data["field"], data["operator"], value)


def criteria_to_data(criteria: Iterable[Criterion]) -> list[dict]:
    return [c.to_data() for c in criteria]


def criteria_from_data(data) -> tuple[Criterion, ...]:
    if not isinstance(data, list):
        raise ValidationError("Critérios mal formados.")
    return tuple(Criterion.from_data(item) for item in data)


@dataclass(frozen=True)
class Segment:
    """Segmento dinâmico: os membros são calculados a partir dos critérios, não guardados.

    Pertencer a um segmento não é autorização para comunicar: a elegibilidade verifica-se
    à parte, pelo consentimento (`domain.consent.is_eligible`).
    """

    id: int | None
    name: str
    description: str
    match_mode: str  # "all" (todos os critérios) ou "any" (qualquer um)
    criteria: tuple[Criterion, ...]
    created_at: datetime | None = None

    def __post_init__(self):
        object.__setattr__(self, "name", clean_name(self.name, "nome do segmento"))
        description = (self.description or "").strip()
        if len(description) > MAX_DESCRIPTION_LENGTH:
            raise ValidationError(
                f"A descrição excede {MAX_DESCRIPTION_LENGTH} caracteres."
            )
        object.__setattr__(self, "description", description)
        if self.match_mode not in MATCH_MODES:
            raise ValidationError("Modo de combinação inválido: use 'all' ou 'any'.")
        criteria = tuple(self.criteria)
        if not 1 <= len(criteria) <= MAX_CRITERIA:
            raise ValidationError(f"Um segmento precisa de 1 a {MAX_CRITERIA} critérios.")
        if not all(isinstance(c, Criterion) for c in criteria):
            raise ValidationError("Critérios inválidos.")
        object.__setattr__(self, "criteria", criteria)

    @property
    def uses_consent(self) -> bool:
        return any(c.is_consent for c in self.criteria)

    def matches(self, contact: Contact, records: Iterable[ConsentRecord] = ()) -> bool:
        records = tuple(records)
        results = (c.matches(contact, records) for c in self.criteria)
        return all(results) if self.match_mode == "all" else any(results)

    def members(
        self,
        contacts: Iterable[Contact],
        records_by_contact: Mapping[int, Iterable[ConsentRecord]] | None = None,
    ) -> list[Contact]:
        by_contact = records_by_contact or {}
        return [c for c in contacts if self.matches(c, by_contact.get(c.id, ()))]
