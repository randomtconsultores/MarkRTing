from dataclasses import dataclass
from datetime import datetime, timedelta

from .consent import PURPOSES
from .contact import ValidationError, clean_name, validate_email

DEFAULT_LANG = "pt"
CONFIRMATION_TTL = timedelta(hours=48)
REQUEST_WINDOW = timedelta(hours=24)
MAX_REQUESTS_PER_DAY = 3


@dataclass(frozen=True)
class SubscriptionRequest:
    id: int
    email: str
    first_name: str
    last_name: str
    lang: str
    purposes: tuple[str, ...]
    created_at: datetime
    delivery_status: str
    attempts: int
    next_attempt_at: datetime
    last_error: str
    skip_reason: str
    sent_at: datetime | None = None
    confirmed_at: datetime | None = None


def is_expired(created_at: datetime, now: datetime) -> bool:
    return now >= created_at + CONFIRMATION_TTL


def validate_subscription(email, first_name, last_name, purposes, lang, languages):
    if not all(isinstance(value, str) for value in (email, first_name, last_name)):
        raise ValidationError("Dados de subscrição inválidos.")
    email = validate_email(email)
    first_name = clean_name(first_name, "nome")
    last_name = clean_name(last_name, "apelido")
    if not isinstance(purposes, (list, tuple)) or not purposes:
        raise ValidationError("Escolha pelo menos uma finalidade.")
    if any(not isinstance(p, str) or p.strip() not in PURPOSES for p in purposes):
        raise ValidationError("Finalidade inválida.")
    chosen = tuple(sorted({p.strip() for p in purposes}))
    lang = lang.strip() if isinstance(lang, str) else ""
    if lang not in languages:
        lang = DEFAULT_LANG
    return email, first_name, last_name, chosen, lang
