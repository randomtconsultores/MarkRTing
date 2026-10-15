"""Processo de envio: `python -m mini_marketo.send_worker [--loop]`.

Lê o SMTP e os limites do ambiente (MINI_MARKETO_SMTP_*), o URL público e a chave das ligações
de cancelamento (MINI_MARKETO_PUBLIC_URL e MINI_MARKETO_SECRET_KEY), usa a mesma base de dados
da aplicação (MINI_MARKETO_DB ou MINI_MARKETO_DATABASE_URL) e envia as mensagens pendentes.
Sem ligação de cancelamento configurada, não arranca. Os endereços suprimidos (bounces,
queixas e supressões manuais) não recebem nada.

Em cada ronda envia primeiro os emails de confirmação de subscrição (com um teto diário próprio,
MINI_MARKETO_CONFIRMATION_DAILY_CAP, e a ligação opcional MINI_MARKETO_PRIVACY_URL) e depois as
campanhas.
"""

import argparse
import logging
import os
import sys
import time

from .database import connect as sqlite_connect
from .database import init_db as sqlite_init_db
from .delivery import DeliveryLimits, DeliveryWorker
from .mailer import SmtpConfig, SmtpMailer
from .main import DEFAULT_DB
from .services.suppression_service import SuppressionService
from .subscription_delivery import (
    ConfirmationSender, confirmation_cap_from_env, load_privacy_url,
)
from .subscription_links import SubscriptionLinks
from .unsubscribe import UnsubscribeLinks

log = logging.getLogger("mini_marketo.send_worker")


def open_repositories(db_path, database_url):
    """Devolve (ligação, campanhas, contactos, consentimentos, supressões), com o esquema já
    migrado. As supressões são já um SuppressionService."""
    if database_url:
        from .postgres_database import connect, init_db
        from .repositories.postgres_campaign_repository import PostgresCampaignRepository
        from .repositories.postgres_consent_repository import PostgresConsentRepository
        from .repositories.postgres_contact_repository import PostgresContactRepository
        from .repositories.postgres_suppression_repository import PostgresSuppressionRepository

        conn = connect(database_url)
        init_db(conn)
        return (
            conn,
            PostgresCampaignRepository(conn),
            PostgresContactRepository(conn),
            PostgresConsentRepository(conn),
            SuppressionService(PostgresSuppressionRepository(conn)),
        )
    from .repositories.campaign_repository import CampaignRepository
    from .repositories.consent_repository import ConsentRepository
    from .repositories.contact_repository import ContactRepository
    from .repositories.suppression_repository import SuppressionRepository

    conn = sqlite_connect(db_path)
    sqlite_init_db(conn)
    return (
        conn,
        CampaignRepository(conn),
        ContactRepository(conn),
        ConsentRepository(conn),
        SuppressionService(SuppressionRepository(conn)),
    )


def open_subscription_repository(conn, database_url):
    """O repositório dos pedidos de subscrição, na mesma ligação das campanhas."""
    if database_url:
        from .repositories.postgres_subscription_repository import (
            PostgresSubscriptionRepository,
        )

        return PostgresSubscriptionRepository(conn)
    from .repositories.subscription_repository import SubscriptionRepository

    return SubscriptionRepository(conn)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m mini_marketo.send_worker",
        description="Envia as mensagens pendentes das campanhas.",
    )
    parser.add_argument("--loop", action="store_true", help="repete até ser interrompido")
    parser.add_argument(
        "--poll-seconds", type=float, default=30.0, help="pausa entre rondas com --loop"
    )
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        config = SmtpConfig.from_env()
        limits = DeliveryLimits.from_env()
        links = UnsubscribeLinks.from_env()
        confirmation_cap = confirmation_cap_from_env()
        privacy_url = load_privacy_url()
    except ValueError as exc:
        print(f"Configuração inválida: {exc}", file=sys.stderr)
        return 2
    database_url = os.environ.get("MINI_MARKETO_DATABASE_URL")
    conn, campaigns, contacts, consents, suppressions = open_repositories(
        DEFAULT_DB, database_url
    )
    subscriptions = open_subscription_repository(conn, database_url)
    mailer = SmtpMailer(config)
    sender = ConfirmationSender(
        subscriptions, suppressions, mailer, limits, SubscriptionLinks(links),
        confirmation_cap, privacy_url,
    )
    worker = DeliveryWorker(campaigns, contacts, consents, mailer, limits, links, suppressions)
    try:
        while True:
            confirmations = sender.run_once()
            log.info(
                "confirmacoes enviadas=%d falhadas=%d repetidas=%d ignoradas=%d suprimidas=%d "
                "paragem=%s",
                confirmations.sent,
                confirmations.failed,
                confirmations.retried,
                confirmations.skipped,
                confirmations.suppressed,
                confirmations.stopped or "-",
            )
            if confirmations.stopped.startswith("erro_fatal"):
                log.error("Envio parado: %s", confirmations.stopped)
                return 2
            summary = worker.run_once()
            log.info(
                "enviadas=%d falhadas=%d repetidas=%d ignoradas=%d suprimidas=%d paragem=%s",
                summary.sent,
                summary.failed,
                summary.retried,
                summary.skipped,
                summary.suppressed,
                summary.stopped or "-",
            )
            if summary.stopped.startswith("erro_fatal"):
                log.error("Envio parado: %s", summary.stopped)
                return 2
            if not args.loop:
                return 0
            time.sleep(args.poll_seconds)
    except KeyboardInterrupt:
        return 0
    finally:
        mailer.close()
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
