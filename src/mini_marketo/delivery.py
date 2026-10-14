import os
import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import timedelta

from .domain.campaign import (
    SKIP_CONTACT_MISSING,
    SKIP_EMAIL_REMOVED,
    SKIP_NO_CONSENT,
    render,
    utc_now,
)
from .domain.consent import is_eligible
from .mailer import SendError, classify


@dataclass(frozen=True)
class DeliveryLimits:
    """Ritmo e tolerância a falhas do envio.

    `daily_cap` serve também para aquecer a reputação de um domínio ou IP novo: sobe-se
    manualmente, de semana a semana.
    """

    rate_per_second: float = 1.0
    daily_cap: int = 200
    max_attempts: int = 5
    batch_size: int = 50
    max_consecutive_failures: int = 10
    stale_claim_seconds: int = 900
    base_backoff_seconds: int = 60
    max_backoff_seconds: int = 3600

    def __post_init__(self):
        if self.rate_per_second <= 0:
            raise ValueError("O ritmo de envio tem de ser positivo.")
        if self.daily_cap < 0:
            raise ValueError("O teto diário não pode ser negativo.")
        if self.max_attempts < 1:
            raise ValueError("São precisas pelo menos 1 tentativa.")
        if self.batch_size < 1:
            raise ValueError("O tamanho do lote tem de ser pelo menos 1.")
        if self.max_consecutive_failures < 1:
            raise ValueError("O limite de falhas consecutivas tem de ser pelo menos 1.")

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "DeliveryLimits":
        env = os.environ if env is None else env

        def number(name, kind, default):
            raw = env.get(name, "").strip()
            if not raw:
                return default
            try:
                return kind(raw)
            except ValueError as exc:
                raise ValueError(f"{name} inválido: {raw!r}.") from exc

        return cls(
            rate_per_second=number("MINI_MARKETO_SMTP_RATE", float, 1.0),
            daily_cap=number("MINI_MARKETO_SMTP_DAILY_CAP", int, 200),
            max_attempts=number("MINI_MARKETO_SMTP_MAX_ATTEMPTS", int, 5),
            batch_size=number("MINI_MARKETO_SMTP_BATCH_SIZE", int, 50),
        )


def backoff_seconds(attempts: int, limits: DeliveryLimits) -> int:
    """Espera antes da tentativa seguinte: dobra a cada tentativa, com teto."""
    return min(limits.base_backoff_seconds * 2 ** max(attempts - 1, 0), limits.max_backoff_seconds)


@dataclass
class RunSummary:
    sent: int = 0
    failed: int = 0
    retried: int = 0
    skipped: int = 0
    stopped: str = ""


class DeliveryWorker:
    """Envia as mensagens pendentes. Nunca corre dentro de um pedido web.

    `campaigns`, `contacts` e `consents` são repositórios do mesmo backend; `mailer` tem
    `send(to, subject, text, html, headers)`; `links` é o `UnsubscribeLinks`, obrigatório: não
    se envia nenhuma mensagem sem ligação de cancelamento. O consentimento verifica-se de novo
    a cada mensagem.
    """

    def __init__(
        self, campaigns, contacts, consents, mailer, limits, links, now=utc_now, sleep=time.sleep
    ):
        if links is None:
            raise ValueError("O envio exige uma ligação de cancelamento de subscrição.")
        self.campaigns = campaigns
        self.contacts = contacts
        self.consents = consents
        self.mailer = mailer
        self.limits = limits
        self.links = links
        self.now = now
        self.sleep = sleep

    def run_once(self) -> RunSummary:
        limits = self.limits
        summary = RunSummary()
        now = self.now()
        self.campaigns.release_stale(now - timedelta(seconds=limits.stale_claim_seconds))
        day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        remaining = limits.daily_cap - self.campaigns.count_sent_since(day_start)
        cache: dict = {}
        streak = 0
        while True:
            if remaining <= 0:
                summary.stopped = "limite_diario"
                return summary
            batch = self.campaigns.claim_due(self.now(), min(limits.batch_size, remaining))
            if not batch:
                return summary
            for index, message in enumerate(batch):
                outcome, detail = self._process(message, cache)
                stop = ""
                if outcome == "sent":
                    summary.sent += 1
                    remaining -= 1
                    streak = 0
                    self.sleep(1 / limits.rate_per_second)
                elif outcome == "skipped":
                    summary.skipped += 1
                elif outcome == "failed":
                    summary.failed += 1
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
                    self.campaigns.release([m.id for m in batch[index + 1:]], undo_attempt=True)
                    summary.stopped = stop
                    return summary

    def _process(self, message, cache):
        campaign = cache.get(message.campaign_id)
        if campaign is None:
            campaign = cache[message.campaign_id] = self.campaigns.get_campaign(message.campaign_id)
        contact = self.contacts.get_contact(message.contact_id)
        if contact is None:
            return self._skip(message, SKIP_CONTACT_MISSING)
        if message.email not in contact.emails:
            return self._skip(message, SKIP_EMAIL_REMOVED)
        if not is_eligible(self.consents.list_for_contact(contact.id), campaign.purpose):
            return self._skip(message, SKIP_NO_CONSENT)
        unsubscribe_url = self.links.url_for(message.id)
        rendered = render(campaign, contact, unsubscribe_url=unsubscribe_url)
        headers = {
            "List-Unsubscribe": f"<{unsubscribe_url}>",
            "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
        }
        try:
            self.mailer.send(
                message.email, rendered.subject, rendered.text, rendered.html, headers
            )
        except Exception as exc:
            return self._on_error(message, exc if isinstance(exc, SendError) else classify(exc))
        self.campaigns.mark_sent(message.id, self.now())
        return "sent", ""

    def _skip(self, message, reason):
        self.campaigns.mark_skipped(message.id, reason)
        return "skipped", reason

    def _on_error(self, message, error: SendError):
        detail = error.detail
        throttled = error.kind == "throttled"
        if error.kind == "fatal":
            self.campaigns.release([message.id], undo_attempt=True)
            return "fatal", detail
        if error.kind == "permanent":
            self.campaigns.mark_failed(message.id, detail)
            return "failed", detail
        if message.attempts >= self.limits.max_attempts:
            self.campaigns.mark_failed(message.id, f"tentativas esgotadas: {detail}")
            return ("throttled" if throttled else "failed"), detail
        retry_at = self.now() + timedelta(seconds=backoff_seconds(message.attempts, self.limits))
        self.campaigns.reschedule(message.id, retry_at, detail)
        return ("throttled" if throttled else "retry"), detail
