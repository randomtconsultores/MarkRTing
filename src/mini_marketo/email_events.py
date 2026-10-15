"""Eventos de email (bounces e queixas) vindos de fora, num formato próprio e simples.

O formato não depende de nenhum fornecedor: um adaptador (por exemplo, para as notificações do
SES) traduz o formato do fornecedor para este. Só um bounce permanente e uma queixa suprimem.
"""

from .domain.contact import ValidationError, validate_email

EVENT_TYPES = ("bounce", "complaint")


def apply_email_event(suppressions, payload) -> str:
    """Aplica um evento. Devolve "suppressed" ou "ignored"; ValidationError se for inválido.

    {"type": "bounce", "email": "...", "permanent": true|false, "detail": "..."}
    {"type": "complaint", "email": "...", "detail": "..."}

    Num bounce, `permanent` é obrigatório e tem de ser booleano: se faltasse, o evento teria de
    ser suprimido ou ignorado às cegas. Um bounce temporário é ignorado (caixa cheia, falha
    passageira). Tudo é validado antes de qualquer escrita.
    """
    if not isinstance(payload, dict):
        raise ValidationError("O evento tem de ser um objeto JSON.")
    kind = payload.get("type")
    if kind not in EVENT_TYPES:
        raise ValidationError("Tipo de evento inválido.")
    email = payload.get("email")
    if not isinstance(email, str):
        raise ValidationError("Email inválido.")
    validate_email(email)
    detail = payload.get("detail", "")
    if detail is None:
        detail = ""
    if not isinstance(detail, str):
        raise ValidationError("O detalhe tem de ser texto.")
    if kind == "bounce":
        permanent = payload.get("permanent")
        if not isinstance(permanent, bool):
            raise ValidationError("Um bounce tem de indicar permanent (true ou false).")
        if not permanent:
            return "ignored"
        reason = "hard_bounce"
    else:
        reason = "complaint"
    suppressions.add(email, reason, "event", detail)
    return "suppressed"
