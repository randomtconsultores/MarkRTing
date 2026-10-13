import os
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .database import connect as sqlite_connect
from .database import init_db as sqlite_init_db
from .domain.contact import ValidationError
from .repositories.contact_repository import ContactRepository
from .services.contact_service import ContactService

BASE = Path(__file__).parent
DEFAULT_DB = Path(os.environ.get("MINI_MARKETO_DB", BASE.parent.parent / "data" / "mini_marketo.db"))

ServiceFactory = Callable[[], AbstractContextManager[ContactService]]


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


def create_app(
    db_path: str | Path = DEFAULT_DB,
    database_url: str | None = None,
    service: ContactService | None = None,
) -> FastAPI:
    if service is not None:
        # Serviço injetado (testes): partilhado, não é aberto nem fechado por pedido.
        @contextmanager
        def open_service():
            yield service
    else:
        open_service = build_service_factory(db_path, database_url)

    templates = Jinja2Templates(directory=str(BASE / "templates"))

    app = FastAPI(title="Mini-Marketo")
    app.mount("/static", StaticFiles(directory=str(BASE / "static")), name="static")

    def get_service() -> Iterator[ContactService]:
        with open_service() as svc:
            yield svc

    Svc = Annotated[ContactService, Depends(get_service)]

    def page(request: Request, service: ContactService, error=None, form=None, status=200):
        return templates.TemplateResponse(
            request, "contacts.html",
            {"contacts": service.list_contacts(), "companies": service.list_companies(),
             "error": error, "form": form or {}},
            status_code=status,
        )

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

    @app.post("/companies")
    def create_company(request: Request, service: Svc, name: str = Form(""),
                       website: str = Form("")):
        try:
            service.create_company(name, website)
        except ValidationError as exc:
            return page(request, service, str(exc), None, 422)
        return RedirectResponse("/contacts", status_code=303)

    return app


def app_factory() -> FastAPI:
    return create_app(database_url=os.environ.get("MINI_MARKETO_DATABASE_URL"))
