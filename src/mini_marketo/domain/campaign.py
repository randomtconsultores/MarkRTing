import re
from dataclasses import dataclass
from datetime import datetime, timezone
from html import escape as escape_html

from .consent import PURPOSES
from .contact import Contact, ValidationError, clean_name

MAX_SUBJECT_LENGTH = 200
MAX_TEXT_LENGTH = 100_000
MAX_HTML_LENGTH = 200_000

CAMPAIGN_STATUSES = {"draft": "Rascunho", "queued": "Enfileirada"}
MESSAGE_STATUSES = {
    "pending": "Pendente",
    "sending": "A enviar",
    "sent": "Enviado",
    "failed": "Falhou",
    "skipped": "Ignorado",
}

SKIP_NO_CONSENT = "sem_consentimento"
SKIP_EMAIL_REMOVED = "email_removido"
SKIP_CONTACT_MISSING = "contacto_inexistente"
SKIP_REASONS = {
    SKIP_NO_CONSENT: "Sem consentimento concedido",
    SKIP_EMAIL_REMOVED: "Email removido do contacto",
    SKIP_CONTACT_MISSING: "Contacto inexistente",
}

# Só estes marcadores são substituídos; qualquer outro texto entre chavetas fica como está.
_PLACEHOLDER = re.compile(r"\{(first_name|last_name)\}")


def utc_now() -> datetime:
    """UTC ingénuo, como o resto do projeto."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


@dataclass(frozen=True)
class Campaign:
    """Campanha de email: conteúdo, finalidade (decide o consentimento exigido) e segmento.

    `segment_name` é uma cópia: a campanha sobrevive a que o segmento seja apagado.
    """

    id: int | None
    name: str
    subject: str
    body_text: str
    body_html: str
    purpose: str
    segment_id: int | None
    segment_name: str
    status: str = "draft"
    created_at: datetime | None = None
    queued_at: datetime | None = None

    def __post_init__(self):
        object.__setattr__(self, "name", clean_name(self.name, "nome da campanha"))
        # Sem quebras de linha: o assunto nunca pode injetar outros cabeçalhos.
        subject = " ".join((self.subject or "").split())
        if not subject:
            raise ValidationError("O assunto é obrigatório.")
        if len(subject) > MAX_SUBJECT_LENGTH:
            raise ValidationError(f"O assunto excede {MAX_SUBJECT_LENGTH} caracteres.")
        object.__setattr__(self, "subject", subject)
        body_text = (self.body_text or "").strip()
        if not body_text:
            raise ValidationError("O texto da mensagem é obrigatório.")
        if len(body_text) > MAX_TEXT_LENGTH:
            raise ValidationError(f"O texto excede {MAX_TEXT_LENGTH} caracteres.")
        object.__setattr__(self, "body_text", body_text)
        body_html = (self.body_html or "").strip()
        if len(body_html) > MAX_HTML_LENGTH:
            raise ValidationError(f"O HTML excede {MAX_HTML_LENGTH} caracteres.")
        object.__setattr__(self, "body_html", body_html)
        if self.purpose not in PURPOSES:
            raise ValidationError("Finalidade inválida.")
        if self.status not in CAMPAIGN_STATUSES:
            raise ValidationError("Estado de campanha inválido.")
        segment_name = " ".join((self.segment_name or "").split())
        if not segment_name:
            raise ValidationError("O nome do segmento é obrigatório.")
        object.__setattr__(self, "segment_name", segment_name)


@dataclass(frozen=True)
class OutboxMessage:
    """Uma mensagem da caixa de saída: um contacto, uma campanha, um estado."""

    id: int
    campaign_id: int
    contact_id: int
    email: str
    status: str
    attempts: int
    next_attempt_at: datetime
    last_error: str
    skip_reason: str
    sent_at: datetime | None = None


@dataclass(frozen=True)
class RenderedEmail:
    subject: str
    text: str
    html: str | None


def _fill(template: str, contact: Contact, escape: bool) -> str:
    values = {"first_name": contact.first_name, "last_name": contact.last_name}

    def replace(match: re.Match) -> str:
        value = values[match.group(1)]
        return escape_html(value) if escape else value

    return _PLACEHOLDER.sub(replace, template)


def _footer_text(url: str) -> str:
    return f"\n\n--\nPara deixar de receber estas mensagens, abra esta ligação: {url}"


def _footer_html(url: str) -> str:
    return (
        '\n<p style="font-size:12px;color:#555">Para deixar de receber estas mensagens, '
        f'<a href="{escape_html(url)}">cancele a subscrição</a>.</p>'
    )


def render(campaign: Campaign, contact: Contact, unsubscribe_url: str | None = None) -> RenderedEmail:
    """Substitui {first_name} e {last_name}. No HTML os valores são escapados; nada é interpretado.

    Com `unsubscribe_url`, acrescenta ao texto e ao HTML (se houver) a ligação para cancelar a
    subscrição.
    """
    text = _fill(campaign.body_text, contact, escape=False)
    html = _fill(campaign.body_html, contact, escape=True) if campaign.body_html else None
    if unsubscribe_url:
        text += _footer_text(unsubscribe_url)
        if html is not None:
            html += _footer_html(unsubscribe_url)
    return RenderedEmail(
        subject=_fill(campaign.subject, contact, escape=False), text=text, html=html
    )
