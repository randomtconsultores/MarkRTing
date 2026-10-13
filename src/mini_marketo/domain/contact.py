import re
from dataclasses import dataclass
from datetime import datetime

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class ValidationError(ValueError):
    pass


class DuplicateEmailError(ValidationError):
    pass


def normalize_email(email: str) -> str:
    return (email or "").strip().lower()


def clean_name(value: str, field: str, required: bool = True) -> str:
    value = " ".join((value or "").split())
    if required and not value:
        raise ValidationError(f"O campo '{field}' é obrigatório.")
    if len(value) > 100:
        raise ValidationError(f"O campo '{field}' excede 100 caracteres.")
    return value


def validate_email(email: str) -> str:
    email = normalize_email(email)
    if len(email) > 254 or not _EMAIL_RE.match(email):
        raise ValidationError("Email inválido.")
    return email


@dataclass(frozen=True)
class Company:
    id: int | None
    name: str
    website: str = ""


@dataclass(frozen=True)
class Contact:
    id: int | None
    email: str
    first_name: str
    last_name: str
    company_id: int | None = None
    company_name: str | None = None
    created_at: datetime | None = None
