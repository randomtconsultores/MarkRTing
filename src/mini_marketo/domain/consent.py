from dataclasses import dataclass
from datetime import datetime

from .contact import ValidationError

PURPOSES = {"newsletter": "Newsletter", "promotional": "Comunicações promocionais"}
DECISIONS = {"granted": "Concedido", "withdrawn": "Retirado"}
SOURCES = {
    "form": "Formulário",
    "admin": "Ação administrativa",
    "unsubscribe": "Cancelamento por email",
}
NO_RECORD = "none"
STATUS_LABELS = {"granted": "Concedido", "withdrawn": "Retirado", NO_RECORD: "Sem registo"}


class ContactNotFoundError(ValidationError):
    pass


@dataclass(frozen=True)
class ConsentRecord:
    """Registo histórico de uma decisão. Nunca é alterado nem apagado."""

    id: int
    contact_id: int
    purpose: str
    decision: str
    source: str
    email: str  # cópia informativa do email principal no momento da decisão
    recorded_at: datetime  # UTC ingénuo, como o resto do projeto


def validate_decision(purpose: str, decision: str, source: str) -> tuple[str, str, str]:
    purpose, decision, source = (purpose or "").strip(), (decision or "").strip(), (source or "").strip()
    if purpose not in PURPOSES:
        raise ValidationError("Finalidade inválida.")
    if decision not in DECISIONS:
        raise ValidationError("Decisão inválida.")
    if source not in SOURCES:
        raise ValidationError("Origem inválida.")
    return purpose, decision, source


def latest_record(records, purpose: str) -> ConsentRecord | None:
    """Último registo da finalidade (maior id). A retirada posterior prevalece."""
    return max((r for r in records if r.purpose == purpose), key=lambda r: r.id, default=None)


def current_status(records, purpose: str) -> str:
    """Estado atual derivado do histórico. Sem registo não é consentimento concedido."""
    latest = latest_record(records, purpose)
    return latest.decision if latest else NO_RECORD


def is_eligible(records, purpose: str) -> bool:
    """Só um consentimento concedido e não retirado autoriza a finalidade."""
    return current_status(records, purpose) == "granted"
