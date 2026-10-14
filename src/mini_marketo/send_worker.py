"""Processo de envio: `python -m mini_marketo.send_worker [--loop]`.

Lê o SMTP e os limites do ambiente (MINI_MARKETO_SMTP_*), o URL público e a chave das ligações
de cancelamento (MINI_MARKETO_PUBLIC_URL e MINI_MARKETO_SECRET_KEY), usa a mesma base de dados
da aplicação (MINI_MARKETO_DB ou MINI_MARKETO_DATABASE_URL) e envia as mensagens pendentes.
Sem ligação de cancelamento configurada, não arranca.
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
from .unsubscribe import UnsubscribeLinks

log = logging.getLogger("mini_marketo.send_worker")


def open_repositories(db_path, database_url):
    """Devolve (ligação, campanhas, contactos, consentimentos), com o esquema já migrado."""
    if database_url:
        from .postgres_database import connect, init_db
        from .repositories.postgres_campaign_repository import PostgresCampaignRepository
        from .repositories.postgres_consent_repository import PostgresConsentRepository
        from .repositories.postgres_contact_repository import PostgresContactRepository

        conn = connect(database_url)
        init_db(conn)
        return (
            conn,
            PostgresCampaignRepository(conn),
            PostgresContactRepository(conn),
            PostgresConsentRepository(conn),
        )
    from .repositories.campaign_repository import CampaignRepository
    from .repositories.consent_repository import ConsentRepository
    from .repositories.contact_repository import ContactRepository

    conn = sqlite_connect(db_path)
    sqlite_init_db(conn)
    return conn, CampaignRepository(conn), ContactRepository(conn), ConsentRepository(conn)


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
    except ValueError as exc:
        print(f"Configuração inválida: {exc}", file=sys.stderr)
        return 2
    conn, campaigns, contacts, consents = open_repositories(
        DEFAULT_DB, os.environ.get("MINI_MARKETO_DATABASE_URL")
    )
    mailer = SmtpMailer(config)
    worker = DeliveryWorker(campaigns, contacts, consents, mailer, limits, links)
    try:
        while True:
            summary = worker.run_once()
            log.info(
                "enviadas=%d falhadas=%d repetidas=%d ignoradas=%d paragem=%s",
                summary.sent,
                summary.failed,
                summary.retried,
                summary.skipped,
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
