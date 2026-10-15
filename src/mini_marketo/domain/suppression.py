from dataclasses import dataclass
from datetime import datetime

from .contact import ValidationError, validate_email

REASONS = {
    "hard_bounce": "Bounce permanente",
    "complaint": "Queixa de spam",
    "manual": "Manual",
}
SOURCES = {
    "smtp": "Resposta do servidor",
    "event": "Evento externo",
    "admin": "Ação administrativa",
}
# Só uma supressão manual pode ser levantada. Bounces e queixas nunca.
REMOVABLE_REASONS = frozenset({"manual"})
MAX_DETAIL = 500


class SuppressionNotFoundError(ValidationError):
    pass


class SuppressionNotRemovableError(ValidationError):
    pass


@dataclass(frozen=True)
class Suppression:
    """Endereço que não pode receber nenhum envio, seja qual for a finalidade."""

    id: int
    email: str
    reason: str
    source: str
    detail: str
    created_at: datetime  # UTC ingénuo, como o resto do projeto


def validate_suppression(email: str, reason: str, source: str, detail: str = "") -> tuple[str, str, str, str]:
    email = validate_email(email)
    reason, source = (reason or "").strip(), (source or "").strip()
    if reason not in REASONS:
        raise ValidationError("Motivo inválido.")
    if source not in SOURCES:
        raise ValidationError("Origem inválida.")
    if (reason == "manual") != (source == "admin"):
        raise ValidationError("Uma supressão manual tem de vir da ação administrativa, e só essa.")
    # O detalhe pode vir de sistemas externos: normaliza-se e limita-se em vez de rejeitar.
    detail = " ".join((detail or "").split())[:MAX_DETAIL]
    return email, reason, source, detail


def is_removable(suppression: Suppression) -> bool:
    return suppression.reason in REMOVABLE_REASONS
