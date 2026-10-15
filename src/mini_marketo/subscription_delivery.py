"""Envio dos emails de confirmação da subscrição, pelo mesmo processo e remetente das campanhas."""

import os
import time
from collections.abc import Mapping
from datetime import timedelta
from urllib.parse import urlsplit

from . import texts
from .delivery import DeliveryLimits, RunSummary, backoff_seconds
from .domain.campaign import SKIP_SUPPRESSED, utc_now
from .domain.subscription import CONFIRMATION_TTL
from .mailer import ConfigError, SendError, classify

DEFAULT_CONFIRMATION_CAP = 200


def confirmation_cap_from_env(env: Mapping[str, str] | None = None) -> int:
    """Teto diário de emails de confirmação. Existe porque o formulário é público: sem teto,
    qualquer pessoa o podia usar para fazer a aplicação enviar email a moradas alheias."""
    env = os.environ if env is None else env
    name = "MINI_MARKETO_CONFIRMATION_DAILY_CAP"
    raw = env.get(name, "").strip()
    if not raw:
        return DEFAULT_CONFIRMATION_CAP
    try:
        cap = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} inválido: {raw!r}.") from exc
    if cap < 0:
        raise ValueError(f"{name} não pode ser negativo.")
    return cap


def load_privacy_url(env: Mapping[str, str] | None = None) -> str:
    """A ligação opcional para a política de privacidade; vazia se não estiver definida."""
    env = os.environ if env is None else env
    raw = env.get("MINI_MARKETO_PRIVACY_URL", "").strip()
    if not raw:
        return ""
    parts = urlsplit(raw)
    if (
        parts.scheme not in ("https", "http")
        or not parts.hostname
        or any(ch.isspace() for ch in raw)
    ):
        raise ConfigError("MINI_MARKETO_PRIVACY_URL tem de ser um URL absoluto (https).")
    return raw


class ConfirmationSender:
    """Envia os emails de confirmação pendentes. Nunca corre dentro de um pedido web.

    `requests` é o repositório de pedidos de subscrição, `suppressions` o SuppressionService e
    `links` o SubscriptionLinks; `mailer` tem `send(to, subject, text, html, headers)`. Os limites
    de ritmo e de tentativas são os das campanhas; o teto diário é próprio (`daily_cap`).
    Um endereço suprimido não recebe confirmação, e uma recusa do servidor que diz que o
    endereço não serve suprime-o, como nas campanhas.
    """

    def __init__(
        self, requests, suppressions, mailer, limits: DeliveryLimits, links, daily_cap: int,
        privacy_url: str = "", now=utc_now, sleep=time.sleep,
    ):
        if suppressions is None:
            raise ValueError("O envio exige a lista de supressão.")
        if links is None:
            raise ValueError("O envio exige as ligações de confirmação.")
        if daily_cap < 0:
            raise ValueError("O teto diário não pode ser negativo.")
        self.requests = requests
        self.suppressions = suppressions
        self.mailer = mailer
        self.limits = limits
        self.links = links
        self.daily_cap = daily_cap
        self.privacy_url = privacy_url
        self.now = now
        self.sleep = sleep

    def run_once(self) -> RunSummary:
        limits = self.limits
        summary = RunSummary()
        now = self.now()
        self.requests.release_stale(now - timedelta(seconds=limits.stale_claim_seconds))
        day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        remaining = self.daily_cap - self.requests.count_sent_since(day_start)
        streak = 0
        while True:
            if remaining <= 0:
                summary.stopped = "limite_diario"
                return summary
            current = self.now()
            batch = self.requests.list_due(
                current, current - CONFIRMATION_TTL, min(limits.batch_size, remaining)
            )
            handled = False
            for request in batch:
                if not self.requests.claim(request.id, self.now()):
                    continue  # outro processo reclamou-o primeiro
                handled = True
                outcome, detail = self._process(request)
                stop = ""
                if outcome == "sent":
                    summary.sent += 1
                    remaining -= 1
                    streak = 0
                    self.sleep(1 / limits.rate_per_second)
                elif outcome == "skipped":
                    summary.skipped += 1
                elif outcome in ("failed", "bounced"):
                    summary.failed += 1
                    if outcome == "bounced":
                        summary.suppressed += 1
                    streak += 1
                elif outcome == "fatal":
                    stop = f"erro_fatal: {detail}"
                else:  # retry ou throttled
                    summary.retried += 1
                    streak += 1
                    if outcome == "throttled":
                        stop = f"limitado_pelo_servidor: {detail}"
                if not stop and streak >= limits.max_consecutive_failures:
                    stop = "falhas_consecutivas"
                if stop:
                    summary.stopped = stop
                    return summary
            if not handled:
                return summary

    def _process(self, request):
        if self.suppressions.is_suppressed(request.email):
            self.requests.mark_skipped(request.id, SKIP_SUPPRESSED)
            return "skipped", SKIP_SUPPRESSED
        lang = request.lang
        purposes = ", ".join(texts.text(lang, f"purpose.{p}") for p in request.purposes)
        privacy = (
            texts.text(lang, "confirm_email.privacy", url=self.privacy_url)
            if self.privacy_url else ""
        )
        subject = texts.text(lang, "confirm_email.subject")
        body = texts.text(
            lang, "confirm_email.text",
            first_name=request.first_name, purposes=purposes,
            hours=int(CONFIRMATION_TTL.total_seconds() // 3600),
            url=self.links.url_for(request.id), privacy=privacy,
        )
        try:
            self.mailer.send(request.email, subject, body)
        except Exception as exc:
            error = exc if isinstance(exc, SendError) else classify(exc)
            return self._on_error(request, error)
        self.requests.mark_sent(request.id, self.now())
        return "sent", ""

    def _on_error(self, request, error: SendError):
        detail = error.detail
        throttled = error.kind == "throttled"
        if error.kind == "fatal":
            self.requests.release(request.id, undo_attempt=True)
            return "fatal", detail
        if error.kind == "permanent":
            if error.address_rejected:
                self.suppressions.add(request.email, "hard_bounce", "smtp", detail)
                self.requests.mark_failed(request.id, detail)
                return "bounced", detail
            self.requests.mark_failed(request.id, detail)
            return "failed", detail
        attempts = request.attempts + 1  # a reclamação já contou esta tentativa
        if attempts >= self.limits.max_attempts:
            self.requests.mark_failed(request.id, f"tentativas esgotadas: {detail}")
            return ("throttled" if throttled else "failed"), detail
        retry_at = self.now() + timedelta(seconds=backoff_seconds(attempts, self.limits))
        self.requests.reschedule(request.id, retry_at, detail)
        return ("throttled" if throttled else "retry"), detail
