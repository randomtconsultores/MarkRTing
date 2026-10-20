import hmac
import json
import os
from collections.abc import Callable, Iterator, Mapping
from contextlib import AbstractContextManager, contextmanager
from itertools import zip_longest
from pathlib import Path
from typing import Annotated
from urllib.parse import urlencode

from fastapi import Depends, FastAPI, Form, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import texts
from .database import connect as sqlite_connect
from .database import init_db as sqlite_init_db
from .domain.campaign import CAMPAIGN_STATUSES, MESSAGE_STATUSES, SKIP_REASONS
from .domain.consent import PURPOSES, ContactNotFoundError
from .domain.contact import ValidationError
from .domain.segment import CONSENT_FIELD_PREFIX, Criterion, Segment
from .domain.subscription import CONFIRMATION_TTL, DEFAULT_LANG
from .domain.suppression import REASONS as SUPPRESSION_REASONS
from .domain.suppression import REMOVABLE_REASONS
from .domain.suppression import SOURCES as SUPPRESSION_SOURCES
from .email_events import apply_email_event
from .mailer import ConfigError
from .pagination import PAGE_SIZES, paginate, parse_page, parse_per_page
from .repositories.campaign_repository import CampaignRepository
from .repositories.consent_repository import ConsentRepository
from .repositories.contact_repository import ContactRepository
from .repositories.segment_repository import SegmentRepository
from .repositories.suppression_repository import SuppressionRepository
from .services.campaign_service import CampaignService
from .services.confirmation_service import ConfirmationService
from .services.consent_service import ConsentService
from .services.contact_service import ContactService
from .services.segment_service import SegmentService
from .services.subscription_service import SubscriptionService
from .services.suppression_service import SuppressionService
from .services.unsubscribe_service import UnsubscribeService
from .subscription_delivery import load_privacy_url
from .subscription_links import SubscriptionLinks
from .unsubscribe import UnsubscribeLinks
from .admin_guard import AdminAccessGuard
from .admin_router import create_access_router
from .services.login_attempt_limiter import LoginAttemptConfig
from .web_security import WebSecurityConfig

BASE = Path(__file__).parent
DEFAULT_DB = Path(os.environ.get("MINI_MARKETO_DB", BASE.parent.parent / "data" / "mini_marketo.db"))
ADMIN_LOGIN_FORM_MAX_BYTES = 4096
ADMIN_BODY_MAX_BYTES = 4_194_304
ADMIN_BODY_MAX_FIELDS = 128

ServiceFactory = Callable[[], AbstractContextManager[ContactService]]
SegmentFactory = Callable[[], AbstractContextManager[SegmentService]]
ConsentFactory = Callable[[], AbstractContextManager[ConsentService]]
CampaignFactory = Callable[[], AbstractContextManager[CampaignService]]
UnsubscribeFactory = Callable[[], AbstractContextManager[UnsubscribeService]]
SuppressionFactory = Callable[[], AbstractContextManager[SuppressionService]]
SubscriptionFactory = Callable[[], AbstractContextManager[SubscriptionService]]
ConfirmationFactory = Callable[[], AbstractContextManager[ConfirmationService | None]]
AccessRepositoryFactory = Callable[[], AbstractContextManager]

EVENTS_TOKEN_MIN_LENGTH = 32
MAX_EVENT_BYTES = 8192

FORM_ROWS = 10
FIELD_LABELS = {
    "first_name": "Nome",
    "last_name": "Apelido",
    "email": "Email",
    "company": "Empresa",
    "created_at": "Criado em (UTC)",
    **{f"{CONSENT_FIELD_PREFIX}{key}": f"Consentimento: {label}" for key, label in PURPOSES.items()},
}
OPERATOR_LABELS = {
    "is": "é",
    "is_not": "não é",
    "contains": "contém",
    "starts_with": "começa por",
    "is_empty": "está vazio",
    "is_not_empty": "não está vazio",
    "before": "antes de",
    "after": "depois de",
}


def criteria_from_rows(fields: list[str], operators: list[str], values: list[str]) -> list[Criterion]:
    """Converte as linhas do formulário em critérios; linhas sem campo são ignoradas."""
    if not (len(fields) == len(operators) == len(values)):
        raise ValidationError("Formulário de critérios inválido.")
    return [Criterion(f, o, v) for f, o, v in zip(fields, operators, values) if f.strip()]


def pad_rows(rows: list[dict]) -> list[dict]:
    """Completa com linhas vazias até FORM_ROWS; nunca corta linhas existentes."""
    blanks = [{"field": "", "operator": "is", "value": ""} for _ in range(FORM_ROWS - len(rows))]
    return rows + blanks


def form_rows(fields=(), operators=(), values=()) -> list[dict]:
    return pad_rows(
        [
            {"field": f, "operator": o or "is", "value": v}
            for f, o, v in zip_longest(fields, operators, values, fillvalue="")
        ]
    )


def new_segment_form() -> dict:
    return {"name": "", "description": "", "match_mode": "all", "rows": form_rows()}


def segment_form(segment: Segment) -> dict:
    rows = [
        {"field": c.field, "operator": c.operator, "value": c.display_value or ""}
        for c in segment.criteria
    ]
    return {
        "name": segment.name,
        "description": segment.description,
        "match_mode": segment.match_mode,
        "rows": pad_rows(rows),
    }


def subscription_lang(value: str | None) -> str:
    """O idioma pedido, se existir no catálogo de textos; senão, o idioma por omissão."""
    return value if value in texts.available_languages() else DEFAULT_LANG


def build_service_factory(db_path: str | Path, database_url: str | None = None) -> ServiceFactory:
    """Devolve uma função que abre um serviço, com ligação própria, por pedido.

    O esquema é criado/migrado uma vez, aqui; cada pedido usa depois a sua ligação,
    que é fechada no fim (commit/rollback não se misturam entre pedidos).
    """
    if database_url:
        # Importação tardia: o psycopg só é necessário quando se usa PostgreSQL.
        from .postgres_database import connect, init_db
        from .repositories.postgres_contact_repository import PostgresContactRepository

        setup_conn = connect(database_url)
        try:
            init_db(setup_conn)
        finally:
            setup_conn.close()

        @contextmanager
        def open_service():
            conn = connect(database_url)
            try:
                yield ContactService(PostgresContactRepository(conn))
            finally:
                conn.close()

        return open_service


    if str(db_path) == ":memory:":
        raise ValueError(
            "':memory:' não serve com uma ligação por pedido (cada ligação veria outra base); "
            "use um ficheiro ou create_app(service=...)."
        )
    setup_conn = sqlite_connect(db_path)
    try:
        sqlite_init_db(setup_conn)
    finally:
        setup_conn.close()

    @contextmanager
    def open_service():
        conn = sqlite_connect(db_path)
        try:
            yield ContactService(ContactRepository(conn))
        finally:
            conn.close()

    return open_service


def build_access_repository_factory(
    db_path: str | Path, database_url: str | None = None
) -> AccessRepositoryFactory:
    """Open a fresh access repository per synchronous operation."""
    if database_url:
        from .postgres_database import connect, init_db
        from .repositories.postgres_access_repository import PostgresAccessRepository

        setup_conn = connect(database_url)
        try:
            init_db(setup_conn)
        finally:
            setup_conn.close()

        @contextmanager
        def open_repository():
            conn = connect(database_url)
            try:
                yield PostgresAccessRepository(conn)
            finally:
                conn.close()

        return open_repository

    if str(db_path) == ":memory:":
        raise ValueError(
            "':memory:' nao serve com repositorios por pedido; use uma base SQLite em ficheiro."
        )
    from .repositories.access_repository import AccessRepository

    setup_conn = sqlite_connect(db_path)
    try:
        sqlite_init_db(setup_conn)
    finally:
        setup_conn.close()

    @contextmanager
    def open_repository():
        conn = sqlite_connect(db_path)
        try:
            yield AccessRepository(conn)
        finally:
            conn.close()

    return open_repository


def build_segment_service_factory(
    db_path: str | Path, database_url: str | None = None
) -> SegmentFactory:
    """Como build_service_factory, mas sem criar/migrar o esquema (já feito por ela)."""
    if database_url:
        from .postgres_database import connect
        from .repositories.postgres_consent_repository import PostgresConsentRepository
        from .repositories.postgres_contact_repository import PostgresContactRepository
        from .repositories.postgres_segment_repository import PostgresSegmentRepository

        @contextmanager
        def open_segments():
            conn = connect(database_url)
            try:
                yield SegmentService(
                    PostgresSegmentRepository(conn),
                    PostgresContactRepository(conn),
                    PostgresConsentRepository(conn),
                )
            finally:
                conn.close()

        return open_segments

    @contextmanager
    def open_segments():
        conn = sqlite_connect(db_path)
        try:
            yield SegmentService(
                SegmentRepository(conn), ContactRepository(conn), ConsentRepository(conn)
            )
        finally:
            conn.close()

    return open_segments


def build_consent_service_factory(
    db_path: str | Path, database_url: str | None = None
) -> ConsentFactory:
    """Sem criar/migrar o esquema (já feito por build_service_factory)."""
    if database_url:
        from .postgres_database import connect
        from .repositories.postgres_consent_repository import PostgresConsentRepository
        from .repositories.postgres_contact_repository import PostgresContactRepository

        @contextmanager
        def open_consents():
            conn = connect(database_url)
            try:
                yield ConsentService(
                    PostgresConsentRepository(conn), PostgresContactRepository(conn)
                )
            finally:
                conn.close()

        return open_consents

    @contextmanager
    def open_consents():
        conn = sqlite_connect(db_path)
        try:
            yield ConsentService(ConsentRepository(conn), ContactRepository(conn))
        finally:
            conn.close()

    return open_consents


def build_campaign_service_factory(
    db_path: str | Path, database_url: str | None = None
) -> CampaignFactory:
    """Sem criar/migrar o esquema (já feito por build_service_factory)."""
    if database_url:
        from .postgres_database import connect
        from .repositories.postgres_campaign_repository import PostgresCampaignRepository
        from .repositories.postgres_consent_repository import PostgresConsentRepository
        from .repositories.postgres_contact_repository import PostgresContactRepository
        from .repositories.postgres_segment_repository import PostgresSegmentRepository
        from .repositories.postgres_suppression_repository import PostgresSuppressionRepository

        @contextmanager
        def open_campaigns():
            conn = connect(database_url)
            try:
                contacts = PostgresContactRepository(conn)
                consents = PostgresConsentRepository(conn)
                segments = SegmentService(PostgresSegmentRepository(conn), contacts, consents)
                suppressions = SuppressionService(PostgresSuppressionRepository(conn))
                yield CampaignService(
                    PostgresCampaignRepository(conn), segments, consents, suppressions
                )
            finally:
                conn.close()

        return open_campaigns

    @contextmanager
    def open_campaigns():
        conn = sqlite_connect(db_path)
        try:
            contacts = ContactRepository(conn)
            consents = ConsentRepository(conn)
            segments = SegmentService(SegmentRepository(conn), contacts, consents)
            suppressions = SuppressionService(SuppressionRepository(conn))
            yield CampaignService(CampaignRepository(conn), segments, consents, suppressions)
        finally:
            conn.close()

    return open_campaigns


def build_suppression_service_factory(
    db_path: str | Path, database_url: str | None = None
) -> SuppressionFactory:
    """Sem criar/migrar o esquema (já feito por build_service_factory)."""
    if database_url:
        from .postgres_database import connect
        from .repositories.postgres_suppression_repository import PostgresSuppressionRepository

        @contextmanager
        def open_suppressions():
            conn = connect(database_url)
            try:
                yield SuppressionService(PostgresSuppressionRepository(conn))
            finally:
                conn.close()

        return open_suppressions

    @contextmanager
    def open_suppressions():
        conn = sqlite_connect(db_path)
        try:
            yield SuppressionService(SuppressionRepository(conn))
        finally:
            conn.close()

    return open_suppressions


def build_subscription_service_factory(
    db_path: str | Path, database_url: str | None = None
) -> SubscriptionFactory:
    """Sem criar/migrar o esquema (já feito por build_service_factory)."""
    languages = texts.available_languages()
    if database_url:
        from .postgres_database import connect
        from .repositories.postgres_subscription_repository import PostgresSubscriptionRepository
        from .repositories.postgres_suppression_repository import PostgresSuppressionRepository

        @contextmanager
        def open_subscriptions():
            conn = connect(database_url)
            try:
                yield SubscriptionService(
                    PostgresSubscriptionRepository(conn),
                    SuppressionService(PostgresSuppressionRepository(conn)),
                    languages,
                )
            finally:
                conn.close()

        return open_subscriptions

    from .repositories.subscription_repository import SubscriptionRepository

    @contextmanager
    def open_subscriptions():
        conn = sqlite_connect(db_path)
        try:
            yield SubscriptionService(
                SubscriptionRepository(conn),
                SuppressionService(SuppressionRepository(conn)),
                languages,
            )
        finally:
            conn.close()

    return open_subscriptions


def build_confirmation_service_factory(
    db_path: str | Path, database_url: str | None = None
) -> ConfirmationFactory:
    """Sem criar/migrar o esquema. Sem URL público e chave não há ligações válidas: devolve None
    e nenhuma confirmação é aceite (como o cancelamento)."""
    base = load_unsubscribe_links()
    if base is None:

        @contextmanager
        def open_nothing():
            yield None

        return open_nothing
    links = SubscriptionLinks(base)
    if database_url:
        from .postgres_database import connect
        from .repositories.postgres_consent_repository import PostgresConsentRepository
        from .repositories.postgres_contact_repository import PostgresContactRepository
        from .repositories.postgres_subscription_repository import PostgresSubscriptionRepository
        from .repositories.postgres_suppression_repository import PostgresSuppressionRepository

        @contextmanager
        def open_confirmation():
            conn = connect(database_url)
            try:
                contacts = PostgresContactRepository(conn)
                yield ConfirmationService(
                    PostgresSubscriptionRepository(conn),
                    SuppressionService(PostgresSuppressionRepository(conn)),
                    ContactService(contacts),
                    ConsentService(PostgresConsentRepository(conn), contacts),
                    links,
                )
            finally:
                conn.close()

        return open_confirmation

    from .repositories.subscription_repository import SubscriptionRepository

    @contextmanager
    def open_confirmation():
        conn = sqlite_connect(db_path)
        try:
            contacts = ContactRepository(conn)
            yield ConfirmationService(
                SubscriptionRepository(conn),
                SuppressionService(SuppressionRepository(conn)),
                ContactService(contacts),
                ConsentService(ConsentRepository(conn), contacts),
                links,
            )
        finally:
            conn.close()

    return open_confirmation


def load_unsubscribe_links() -> UnsubscribeLinks | None:
    """None se o URL público ou a chave não estão configurados: nenhuma ligação é aceite."""
    try:
        return UnsubscribeLinks.from_env()
    except ConfigError:
        return None


def check_events_token(token: str | None) -> str | None:
    """None (ou vazio) desativa o endpoint de eventos. Um token curto é um erro de configuração:
    desativar em silêncio esconderia o problema, e aceitá-lo deixaria o endpoint fraco."""
    token = (token or "").strip()
    if not token:
        return None
    if len(token) < EVENTS_TOKEN_MIN_LENGTH:
        raise ConfigError(
            f"MINI_MARKETO_EVENTS_TOKEN tem de ter pelo menos {EVENTS_TOKEN_MIN_LENGTH} caracteres."
        )
    return token


def load_events_token(env: Mapping[str, str] | None = None) -> str | None:
    env = os.environ if env is None else env
    return check_events_token(env.get("MINI_MARKETO_EVENTS_TOKEN", ""))


def bearer_matches(header: str | None, token: str) -> bool:
    scheme, _, value = (header or "").partition(" ")
    value = value.strip()
    if scheme.lower() != "bearer" or not value:
        return False
    return hmac.compare_digest(value.encode("utf-8"), token.encode("utf-8"))


def build_unsubscribe_service_factory(
    db_path: str | Path, database_url: str | None = None
) -> UnsubscribeFactory:
    """Sem criar/migrar o esquema (já feito por build_service_factory)."""
    links = load_unsubscribe_links()
    if database_url:
        from .postgres_database import connect
        from .repositories.postgres_campaign_repository import PostgresCampaignRepository
        from .repositories.postgres_consent_repository import PostgresConsentRepository

        @contextmanager
        def open_unsubscribe():
            conn = connect(database_url)
            try:
                yield UnsubscribeService(
                    PostgresCampaignRepository(conn), PostgresConsentRepository(conn), links
                )
            finally:
                conn.close()

        return open_unsubscribe

    @contextmanager
    def open_unsubscribe():
        conn = sqlite_connect(db_path)
        try:
            yield UnsubscribeService(CampaignRepository(conn), ConsentRepository(conn), links)
        finally:
            conn.close()

    return open_unsubscribe


def create_app(
    db_path: str | Path = DEFAULT_DB,
    database_url: str | None = None,
    service: ContactService | None = None,
    segment_service: SegmentService | None = None,
    consent_service: ConsentService | None = None,
    campaign_service: CampaignService | None = None,
    unsubscribe_service: UnsubscribeService | None = None,
    suppression_service: SuppressionService | None = None,
    events_token: str | None = None,
    subscription_service: SubscriptionService | None = None,
    confirmation_service: ConfirmationService | None = None,
    privacy_url: str | None = None,
    *,
    web_security_config: WebSecurityConfig | None = None,
    login_attempt_config: LoginAttemptConfig | None = None,
    access_repo_factory: AccessRepositoryFactory | None = None,
    access_clock=None,
) -> FastAPI:
    web_config = web_security_config or WebSecurityConfig.from_env()
    login_config = login_attempt_config or LoginAttemptConfig.from_env()
    if not isinstance(web_config, WebSecurityConfig) or not isinstance(
        login_config, LoginAttemptConfig
    ):
        raise ValueError("Configuracao de acesso invalida.")
    if hmac.compare_digest(web_config.csrf_key, login_config.hmac_key):
        raise ValueError("As chaves CSRF e de limitacao devem ser independentes.")
    if access_clock is not None and not callable(access_clock):
        raise ValueError("O relogio de acesso deve ser invocavel.")
    injected_services = any(
        item is not None
        for item in (
            service,
            segment_service,
            consent_service,
            campaign_service,
            unsubscribe_service,
            suppression_service,
            subscription_service,
            confirmation_service,
        )
    )
    if injected_services and service is None:
        raise ValueError("A injecao de servicos requer o servico de contactos.")
    if injected_services and access_repo_factory is None:
        raise ValueError(
            "Servicos injetados requerem uma fabrica explicita do repositorio de acesso."
        )
    if access_repo_factory is not None and not callable(access_repo_factory):
        raise ValueError("A fabrica do repositorio de acesso deve ser invocavel.")

    # Sem token explícito lê-se o ambiente; "" desativa o endpoint (útil nos testes).
    token = check_events_token(events_token) if events_token is not None else load_events_token()
    # Idem para a ligação da política de privacidade; "" não mostra nenhuma.
    privacy = (
        load_privacy_url()
        if privacy_url is None
        else load_privacy_url({"MINI_MARKETO_PRIVACY_URL": privacy_url})
    )
    if service is not None:
        # Serviços injetados (testes): partilhados, não são abertos nem fechados por pedido.
        @contextmanager
        def open_service():
            yield service

        @contextmanager
        def open_segments():
            if segment_service is None:
                raise RuntimeError("create_app: segment_service não foi injetado.")
            yield segment_service

        @contextmanager
        def open_consents():
            if consent_service is None:
                raise RuntimeError("create_app: consent_service não foi injetado.")
            yield consent_service

        @contextmanager
        def open_campaigns():
            if campaign_service is None:
                raise RuntimeError("create_app: campaign_service não foi injetado.")
            yield campaign_service

        @contextmanager
        def open_unsubscribe():
            if unsubscribe_service is None:
                raise RuntimeError("create_app: unsubscribe_service não foi injetado.")
            yield unsubscribe_service

        @contextmanager
        def open_suppressions():
            if suppression_service is None:
                raise RuntimeError("create_app: suppression_service não foi injetado.")
            yield suppression_service

        @contextmanager
        def open_subscriptions():
            if subscription_service is None:
                raise RuntimeError("create_app: subscription_service não foi injetado.")
            yield subscription_service

        @contextmanager
        def open_confirmation():
            if confirmation_service is None:
                raise RuntimeError("create_app: confirmation_service não foi injetado.")
            yield confirmation_service
    else:
        open_service = build_service_factory(db_path, database_url)
        open_segments = build_segment_service_factory(db_path, database_url)
        open_consents = build_consent_service_factory(db_path, database_url)
        open_campaigns = build_campaign_service_factory(db_path, database_url)
        open_unsubscribe = build_unsubscribe_service_factory(db_path, database_url)
        open_suppressions = build_suppression_service_factory(db_path, database_url)
        open_subscriptions = build_subscription_service_factory(db_path, database_url)
        open_confirmation = build_confirmation_service_factory(db_path, database_url)

    access_factory = access_repo_factory or build_access_repository_factory(
        db_path, database_url
    )
    templates = Jinja2Templates(directory=str(BASE / "templates"))

    app = FastAPI(title="Mini-Marketo")
    app.state.access_repo_factory = access_factory
    app.mount("/static", StaticFiles(directory=str(BASE / "static")), name="static")
    app.include_router(
        create_access_router(
            access_factory,
            web_config=web_config,
            login_config=login_config,
            max_form_bytes=ADMIN_LOGIN_FORM_MAX_BYTES,
            clock=access_clock,
        )
    )
    app.add_middleware(
        AdminAccessGuard,
        repo_factory=access_factory,
        web_config=web_config,
        max_body_bytes=ADMIN_BODY_MAX_BYTES,
        max_form_fields=ADMIN_BODY_MAX_FIELDS,
        clock=access_clock,
    )

    def admin_access_context(request: Request) -> dict:
        state = request.scope.get("state")
        context = state.get("admin_access") if isinstance(state, dict) else None
        if (
            not isinstance(context, dict)
            or context.get("role") not in ("admin", "viewer")
            or not isinstance(context.get("admin_csrf_token"), str)
            or (
                context["role"] == "admin"
                and not context["admin_csrf_token"]
            )
            or not isinstance(context.get("logout_csrf_token"), str)
            or not context["logout_csrf_token"]
        ):
            raise HTTPException(status_code=503, detail="Contexto administrativo indisponivel.")
        return context

    def get_service() -> Iterator[ContactService]:
        with open_service() as svc:
            yield svc

    def get_segments() -> Iterator[SegmentService]:
        with open_segments() as svc:
            yield svc

    def get_consents() -> Iterator[ConsentService]:
        with open_consents() as svc:
            yield svc

    def get_campaigns() -> Iterator[CampaignService]:
        with open_campaigns() as svc:
            yield svc

    def get_unsubscribe() -> Iterator[UnsubscribeService]:
        with open_unsubscribe() as svc:
            yield svc

    def get_suppressions() -> Iterator[SuppressionService]:
        with open_suppressions() as svc:
            yield svc

    def get_subscriptions() -> Iterator[SubscriptionService]:
        with open_subscriptions() as svc:
            yield svc

    def get_confirmation() -> Iterator[ConfirmationService | None]:
        with open_confirmation() as svc:
            yield svc

    Svc = Annotated[ContactService, Depends(get_service)]
    Seg = Annotated[SegmentService, Depends(get_segments)]
    Cons = Annotated[ConsentService, Depends(get_consents)]
    Camp = Annotated[CampaignService, Depends(get_campaigns)]
    Unsub = Annotated[UnsubscribeService, Depends(get_unsubscribe)]
    Supp = Annotated[SuppressionService, Depends(get_suppressions)]
    Subs = Annotated[SubscriptionService, Depends(get_subscriptions)]
    # Pode ser None (sem URL público e chave): as rotas tratam-no como ligação inválida.
    Conf = Annotated[ConfirmationService, Depends(get_confirmation)]

    def page(request: Request, service: ContactService, error=None, form=None, status=200):
        return templates.TemplateResponse(
            request, "contacts.html",
            {"admin_access": admin_access_context(request),
             "contacts": service.list_contacts(), "companies": service.list_companies(),
             "error": error, "form": form or {}},
            status_code=status,
        )

    def segments_page(request: Request, segments: SegmentService, error=None, form=None, status=200):
        return templates.TemplateResponse(
            request, "segments.html",
            {"admin_access": admin_access_context(request),
             "segments": segments.list_with_counts(), "error": error,
             "form": form or new_segment_form(),
             "fields": FIELD_LABELS, "operators": OPERATOR_LABELS,
             "form_action": "/segments", "submit_label": "Guardar"},
            status_code=status,
        )

    def edit_page(request: Request, segment: Segment, form: dict, error=None, status=200):
        return templates.TemplateResponse(
            request, "segment_edit.html",
            {"admin_access": admin_access_context(request),
             "segment": segment, "error": error, "form": form,
             "fields": FIELD_LABELS, "operators": OPERATOR_LABELS,
             "form_action": f"/segments/{segment.id}/edit",
             "submit_label": "Guardar alterações"},
            status_code=status,
        )

    def consent_page(request: Request, consents: ConsentService, contact_id: int,
                     error=None, status=200):
        try:
            view = consents.view(contact_id)
        except ContactNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return templates.TemplateResponse(
            request, "consent.html",
            {**view, "admin_access": admin_access_context(request), "error": error},
            status_code=status
        )

    def campaigns_page(request: Request, campaigns: CampaignService, error=None, form=None,
                       status=200):
        return templates.TemplateResponse(
            request, "campaigns.html",
            {"admin_access": admin_access_context(request),
             "campaigns": campaigns.list_campaigns(), "segments": campaigns.list_segments(),
             "purposes": PURPOSES, "statuses": CAMPAIGN_STATUSES,
             "error": error, "form": form or {}},
            status_code=status,
        )

    def campaign_page(request: Request, campaigns: CampaignService, campaign_id: int,
                      error=None, status=200):
        try:
            view = campaigns.summary(campaign_id)
        except ValidationError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return templates.TemplateResponse(
            request, "campaign.html",
            {**view, "admin_access": admin_access_context(request),
             "purposes": PURPOSES, "statuses": CAMPAIGN_STATUSES,
             "message_statuses": MESSAGE_STATUSES, "skip_reasons": SKIP_REASONS,
             "error": error},
            status_code=status,
        )

    def suppressions_page(request: Request, suppressions: SuppressionService, error=None,
                          form=None, status=200):
        return templates.TemplateResponse(
            request, "suppressions.html",
            {"admin_access": admin_access_context(request),
             "suppressions": suppressions.list(), "reasons": SUPPRESSION_REASONS,
             "sources": SUPPRESSION_SOURCES, "removable": REMOVABLE_REASONS,
             "error": error, "form": form or {}},
            status_code=status,
        )

    def unsubscribe_response(request: Request, view, token: str, done: bool):
        # A resposta é sempre 200, sem redirecionar (o cancelamento com um clique do RFC 8058 não
        # segue redirecionamentos). O token está no URL: nem cache nem Referer o devem propagar.
        response = templates.TemplateResponse(
            request, "unsubscribe.html",
            {"view": view, "token": token, "done": done, "purposes": PURPOSES},
        )
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        return response

    def public_response(request: Request, template: str, lang: str, context: dict, status=200):
        """Páginas públicas da subscrição: autónomas, sem o menu de administração, com os
        textos do catálogo no idioma `lang`. Algumas têm o token no URL: nem cache nem Referer."""
        context = {
            **context, "lang": lang,
            "t": lambda key, **params: texts.text(lang, key, **params),
        }
        response = templates.TemplateResponse(request, template, context, status_code=status)
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        return response

    def subscribe_page(request: Request, lang: str, error=False, form=None, status=200):
        return public_response(
            request, "subscribe.html", lang,
            {"purposes": list(PURPOSES), "privacy_url": privacy, "error": error,
             "form": form or {"first_name": "", "last_name": "", "email": "", "purposes": []}},
            status,
        )

    def notice_page(request: Request, lang: str, title: str, body: str, status=200,
                    action: str = "", button: str = ""):
        return public_response(
            request, "subscribe_notice.html", lang,
            {"title": title, "body": body, "action": action, "button": button},
            status,
        )

    def confirmation_page(request: Request, view, token: str, done: bool):
        if view is None:
            # Token inválido, pedido inexistente, expirado, suprimido ou ligações por configurar:
            # a mesma resposta, para não dizer o que falhou.
            return notice_page(
                request, DEFAULT_LANG,
                texts.text(DEFAULT_LANG, "invalid.title"),
                texts.text(DEFAULT_LANG, "invalid.body"), 404,
            )
        lang = subscription_lang(view.request.lang)
        purposes = ", ".join(texts.text(lang, f"purpose.{p}") for p in view.request.purposes)
        if done or view.confirmed:
            return notice_page(
                request, lang, texts.text(lang, "confirmed.title"),
                texts.text(lang, "confirmed.body", purposes=purposes),
            )
        return notice_page(
            request, lang, texts.text(lang, "confirm.title"),
            texts.text(lang, "confirm.body", purposes=purposes),
            action=f"/subscribe/confirm/{token}", button=texts.text(lang, "confirm.button"),
        )

    def load_segment(segments: SegmentService, segment_id: int) -> Segment:
        try:
            return segments.get_segment(segment_id)
        except ValidationError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/")
    def home():
        return RedirectResponse("/contacts", status_code=303)

    @app.get("/contacts")
    def contacts(request: Request, service: Svc):
        return page(request, service)

    @app.post("/contacts")
    def create_contact(request: Request, service: Svc, first_name: str = Form(""),
                       last_name: str = Form(""), email: str = Form(""),
                       company_id: str = Form("")):
        form = {"first_name": first_name, "last_name": last_name,
                "email": email, "company_id": company_id}
        try:
            cid = int(company_id) if company_id.strip() else None
            service.create_contact(first_name, last_name, email, cid)
        except ValueError as exc:  # ValidationError herda de ValueError
            msg = str(exc) if isinstance(exc, ValidationError) else "Empresa inválida."
            return page(request, service, msg, form, 422)
        return RedirectResponse("/contacts", status_code=303)

    def email_action(request: Request, service: ContactService, action):
        try:
            action()
        except ValidationError as exc:
            return page(request, service, str(exc), None, 422)
        return RedirectResponse("/contacts", status_code=303)

    @app.post("/contacts/{contact_id}/emails")
    def add_email(request: Request, service: Svc, contact_id: int, email: str = Form("")):
        return email_action(request, service, lambda: service.add_email(contact_id, email))

    @app.post("/contacts/{contact_id}/emails/primary")
    def set_primary_email(request: Request, service: Svc, contact_id: int, email: str = Form("")):
        return email_action(
            request, service, lambda: service.set_primary_email(contact_id, email)
        )

    @app.post("/contacts/{contact_id}/emails/remove")
    def remove_email(request: Request, service: Svc, contact_id: int, email: str = Form("")):
        return email_action(request, service, lambda: service.remove_email(contact_id, email))

    @app.get("/contacts/{contact_id}/consent")
    def consent_view(request: Request, consents: Cons, contact_id: int):
        return consent_page(request, consents, contact_id)

    @app.post("/contacts/{contact_id}/consent")
    def consent_record(request: Request, consents: Cons, contact_id: int,
                       purpose: str = Form(""), decision: str = Form("")):
        # A origem é fixada no servidor: esta interface é uma ação administrativa.
        try:
            consents.record(contact_id, purpose, decision, "admin")
        except ContactNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValidationError as exc:
            return consent_page(request, consents, contact_id, str(exc), 422)
        return RedirectResponse(f"/contacts/{contact_id}/consent", status_code=303)

    @app.post("/companies")
    def create_company(request: Request, service: Svc, name: str = Form(""),
                       website: str = Form("")):
        try:
            service.create_company(name, website)
        except ValidationError as exc:
            return page(request, service, str(exc), None, 422)
        return RedirectResponse("/contacts", status_code=303)

    @app.get("/segments")
    def segments_list(request: Request, segments: Seg):
        return segments_page(request, segments)

    @app.post("/segments")
    def create_segment(
        request: Request,
        segments: Seg,
        name: str = Form(""),
        description: str = Form(""),
        match_mode: str = Form("all"),
        criterion_field: list[str] = Form([]),
        criterion_operator: list[str] = Form([]),
        criterion_value: list[str] = Form([]),
    ):
        form = {
            "name": name,
            "description": description,
            "match_mode": match_mode,
            "rows": form_rows(criterion_field, criterion_operator, criterion_value),
        }
        try:
            criteria = criteria_from_rows(criterion_field, criterion_operator, criterion_value)
            created = segments.create_segment(name, description, match_mode, criteria)
        except ValidationError as exc:
            return segments_page(request, segments, str(exc), form, 422)
        return RedirectResponse(f"/segments/{created.id}", status_code=303)

    @app.get("/segments/{segment_id}")
    def segment_detail(
        request: Request,
        segments: Seg,
        segment_id: int,
        page_number: str = Query("1", alias="page"),
        per_page: str = Query(""),
    ):
        # Lidos como texto: um valor inválido volta ao valor por omissão em vez de dar erro 422.
        segment = load_segment(segments, segment_id)
        members = paginate(
            segments.members(segment_id), parse_page(page_number), parse_per_page(per_page)
        )
        return templates.TemplateResponse(
            request, "segment.html",
            {"admin_access": admin_access_context(request),
             "segment": segment, "members": members, "sizes": PAGE_SIZES,
             "fields": FIELD_LABELS, "operators": OPERATOR_LABELS},
        )

    @app.get("/segments/{segment_id}/edit")
    def segment_edit_form(request: Request, segments: Seg, segment_id: int):
        segment = load_segment(segments, segment_id)
        return edit_page(request, segment, segment_form(segment))

    @app.post("/segments/{segment_id}/edit")
    def segment_edit(
        request: Request,
        segments: Seg,
        segment_id: int,
        name: str = Form(""),
        description: str = Form(""),
        match_mode: str = Form("all"),
        criterion_field: list[str] = Form([]),
        criterion_operator: list[str] = Form([]),
        criterion_value: list[str] = Form([]),
    ):
        segment = load_segment(segments, segment_id)
        form = {
            "name": name,
            "description": description,
            "match_mode": match_mode,
            "rows": form_rows(criterion_field, criterion_operator, criterion_value),
        }
        try:
            criteria = criteria_from_rows(criterion_field, criterion_operator, criterion_value)
            segments.update_segment(segment_id, name, description, match_mode, criteria)
        except ValidationError as exc:
            return edit_page(request, segment, form, str(exc), 422)
        return RedirectResponse(f"/segments/{segment_id}", status_code=303)

    @app.post("/segments/{segment_id}/delete")
    def delete_segment(segments: Seg, segment_id: int):
        try:
            segments.delete_segment(segment_id)
        except ValidationError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return RedirectResponse("/segments", status_code=303)

    @app.get("/campaigns")
    def campaigns_list(request: Request, campaigns: Camp):
        return campaigns_page(request, campaigns)

    @app.post("/campaigns")
    def create_campaign(
        request: Request,
        campaigns: Camp,
        name: str = Form(""),
        subject: str = Form(""),
        body_text: str = Form(""),
        body_html: str = Form(""),
        purpose: str = Form(""),
        segment_id: str = Form(""),
    ):
        form = {"name": name, "subject": subject, "body_text": body_text,
                "body_html": body_html, "purpose": purpose, "segment_id": segment_id}
        try:
            sid = int(segment_id)
        except ValueError:
            return campaigns_page(request, campaigns, "Escolha um segmento.", form, 422)
        try:
            created = campaigns.create_campaign(name, subject, body_text, body_html, purpose, sid)
        except ValidationError as exc:
            return campaigns_page(request, campaigns, str(exc), form, 422)
        return RedirectResponse(f"/campaigns/{created.id}", status_code=303)

    @app.get("/campaigns/{campaign_id}")
    def campaign_detail(request: Request, campaigns: Camp, campaign_id: int):
        return campaign_page(request, campaigns, campaign_id)

    @app.post("/campaigns/{campaign_id}/enqueue")
    def campaign_enqueue(request: Request, campaigns: Camp, campaign_id: int):
        try:
            campaigns.get_campaign(campaign_id)
        except ValidationError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        try:
            campaigns.enqueue(campaign_id)
        except ValidationError as exc:
            return campaign_page(request, campaigns, campaign_id, str(exc), 422)
        return RedirectResponse(f"/campaigns/{campaign_id}", status_code=303)

    @app.get("/suppressions")
    def suppressions_list(request: Request, suppressions: Supp):
        return suppressions_page(request, suppressions)

    @app.post("/suppressions")
    def suppression_add(request: Request, suppressions: Supp, email: str = Form(""),
                        detail: str = Form("")):
        # Origem e motivo são fixados no servidor: esta interface é uma ação administrativa.
        form = {"email": email, "detail": detail}
        try:
            suppressions.add(email, "manual", "admin", detail)
        except ValidationError as exc:
            return suppressions_page(request, suppressions, str(exc), form, 422)
        return RedirectResponse("/suppressions", status_code=303)

    @app.post("/suppressions/remove")
    def suppression_remove(request: Request, suppressions: Supp, email: str = Form("")):
        try:
            suppressions.remove(email)
        except ValidationError as exc:
            return suppressions_page(request, suppressions, str(exc), None, 422)
        return RedirectResponse("/suppressions", status_code=303)

    @app.post("/events/email")
    async def email_event(request: Request):
        # Chamado por um sistema externo (público). Desativado sem token (404); com token, só
        # aceita `Authorization: Bearer`. A ligação à base de dados só abre depois de autenticar.
        if token is None:
            raise HTTPException(status_code=404, detail="Not Found")
        if not bearer_matches(request.headers.get("authorization"), token):
            raise HTTPException(
                status_code=401, detail="Não autorizado.", headers={"WWW-Authenticate": "Bearer"}
            )
        declared = request.headers.get("content-length", "")
        if declared.isdigit() and int(declared) > MAX_EVENT_BYTES:
            raise HTTPException(status_code=413, detail="Pedido demasiado grande.")
        body = await request.body()
        if len(body) > MAX_EVENT_BYTES:
            raise HTTPException(status_code=413, detail="Pedido demasiado grande.")
        try:
            payload = json.loads(body)
        except ValueError as exc:  # inclui UnicodeDecodeError
            raise HTTPException(status_code=422, detail="JSON inválido.") from exc

        def record():
            with open_suppressions() as svc:
                return apply_email_event(svc, payload)

        try:
            outcome = await run_in_threadpool(record)
        except ValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return JSONResponse({"status": outcome})

    @app.get("/subscribe")
    def subscribe_form(request: Request, lang: str = Query("")):
        return subscribe_page(request, subscription_lang(lang))

    @app.post("/subscribe")
    def subscribe_submit(
        request: Request,
        subscriptions: Subs,
        first_name: str = Form(""),
        last_name: str = Form(""),
        email: str = Form(""),
        purposes: list[str] = Form([]),
        lang: str = Form(""),
        website: str = Form(""),
    ):
        # Endereço público. Todos os pedidos válidos têm a mesma resposta (gravados, suprimidos,
        # limitados, de robôs ou de endereços já conhecidos), para não revelar nada sobre um
        # endereço. Só um formulário inválido dá erro, e esse erro só fala do que a pessoa escreveu.
        lang = subscription_lang(lang)
        received = "/subscribe/received" + (
            "" if lang == DEFAULT_LANG else "?" + urlencode({"lang": lang})
        )
        if website.strip():  # campo-armadilha: só um robô o preenche
            return RedirectResponse(received, status_code=303)
        try:
            subscriptions.request(email, first_name, last_name, purposes, lang)
        except ValidationError:
            form = {"first_name": first_name, "last_name": last_name, "email": email,
                    "purposes": purposes}
            return subscribe_page(request, lang, error=True, form=form, status=422)
        return RedirectResponse(received, status_code=303)

    @app.get("/subscribe/received")
    def subscribe_received(request: Request, lang: str = Query("")):
        lang = subscription_lang(lang)
        hours = int(CONFIRMATION_TTL.total_seconds() // 3600)
        return notice_page(
            request, lang, texts.text(lang, "received.title"),
            texts.text(lang, "received.body", hours=hours),
        )

    @app.get("/subscribe/confirm/{token}")
    def subscribe_confirm_page(request: Request, confirmation: Conf, token: str):
        # Só mostra a confirmação: quem faz GET (verificadores de ligações) não confirma ninguém.
        view = confirmation.preview(token) if confirmation is not None else None
        return confirmation_page(request, view, token, done=False)

    @app.post("/subscribe/confirm/{token}")
    def subscribe_confirm_now(request: Request, confirmation: Conf, token: str):
        view = confirmation.confirm(token) if confirmation is not None else None
        return confirmation_page(request, view, token, done=True)

    @app.get("/unsubscribe/{token}")
    def unsubscribe_confirm_page(request: Request, unsub: Unsub, token: str):
        # Só mostra a confirmação: quem faz GET (por exemplo, verificadores de ligações) não cancela nada.
        view = unsub.preview(token)
        if view is None:
            raise HTTPException(status_code=404, detail="Ligação inválida ou expirada.")
        return unsubscribe_response(request, view, token, done=False)

    @app.post("/unsubscribe/{token}")
    def unsubscribe_now(request: Request, unsub: Unsub, token: str):
        # Sem campos obrigatórios: o cancelamento com um clique (RFC 8058) envia
        # `List-Unsubscribe=One-Click` e o formulário da página não envia nada.
        view = unsub.unsubscribe(token)
        if view is None:
            raise HTTPException(status_code=404, detail="Ligação inválida ou expirada.")
        return unsubscribe_response(request, view, token, done=True)

    return app


def app_factory() -> FastAPI:
    return create_app(database_url=os.environ.get("MINI_MARKETO_DATABASE_URL"))
