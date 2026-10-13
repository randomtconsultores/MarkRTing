import os
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from itertools import zip_longest
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, Form, HTTPException, Query, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .database import connect as sqlite_connect
from .database import init_db as sqlite_init_db
from .domain.contact import ValidationError
from .domain.segment import Criterion, Segment
from .pagination import PAGE_SIZES, paginate, parse_page, parse_per_page
from .repositories.contact_repository import ContactRepository
from .repositories.segment_repository import SegmentRepository
from .services.contact_service import ContactService
from .services.segment_service import SegmentService

BASE = Path(__file__).parent
DEFAULT_DB = Path(os.environ.get("MINI_MARKETO_DB", BASE.parent.parent / "data" / "mini_marketo.db"))

ServiceFactory = Callable[[], AbstractContextManager[ContactService]]
SegmentFactory = Callable[[], AbstractContextManager[SegmentService]]

FORM_ROWS = 10
FIELD_LABELS = {
    "first_name": "Nome",
    "last_name": "Apelido",
    "email": "Email",
    "company": "Empresa",
    "created_at": "Criado em (UTC)",
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
        {"field": c.field, "operator": c.operator, "value": c.value or ""}
        for c in segment.criteria
    ]
    return {
        "name": segment.name,
        "description": segment.description,
        "match_mode": segment.match_mode,
        "rows": pad_rows(rows),
    }


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


def build_segment_service_factory(
    db_path: str | Path, database_url: str | None = None
) -> SegmentFactory:
    """Como build_service_factory, mas sem criar/migrar o esquema (já feito por ela)."""
    if database_url:
        from .postgres_database import connect
        from .repositories.postgres_contact_repository import PostgresContactRepository
        from .repositories.postgres_segment_repository import PostgresSegmentRepository

        @contextmanager
        def open_segments():
            conn = connect(database_url)
            try:
                yield SegmentService(
                    PostgresSegmentRepository(conn), PostgresContactRepository(conn)
                )
            finally:
                conn.close()

        return open_segments

    @contextmanager
    def open_segments():
        conn = sqlite_connect(db_path)
        try:
            yield SegmentService(SegmentRepository(conn), ContactRepository(conn))
        finally:
            conn.close()

    return open_segments


def create_app(
    db_path: str | Path = DEFAULT_DB,
    database_url: str | None = None,
    service: ContactService | None = None,
    segment_service: SegmentService | None = None,
) -> FastAPI:
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
    else:
        open_service = build_service_factory(db_path, database_url)
        open_segments = build_segment_service_factory(db_path, database_url)

    templates = Jinja2Templates(directory=str(BASE / "templates"))

    app = FastAPI(title="Mini-Marketo")
    app.mount("/static", StaticFiles(directory=str(BASE / "static")), name="static")

    def get_service() -> Iterator[ContactService]:
        with open_service() as svc:
            yield svc

    def get_segments() -> Iterator[SegmentService]:
        with open_segments() as svc:
            yield svc

    Svc = Annotated[ContactService, Depends(get_service)]
    Seg = Annotated[SegmentService, Depends(get_segments)]

    def page(request: Request, service: ContactService, error=None, form=None, status=200):
        return templates.TemplateResponse(
            request, "contacts.html",
            {"contacts": service.list_contacts(), "companies": service.list_companies(),
             "error": error, "form": form or {}},
            status_code=status,
        )

    def segments_page(request: Request, segments: SegmentService, error=None, form=None, status=200):
        return templates.TemplateResponse(
            request, "segments.html",
            {"segments": segments.list_with_counts(), "error": error,
             "form": form or new_segment_form(),
             "fields": FIELD_LABELS, "operators": OPERATOR_LABELS,
             "form_action": "/segments", "submit_label": "Guardar"},
            status_code=status,
        )

    def edit_page(request: Request, segment: Segment, form: dict, error=None, status=200):
        return templates.TemplateResponse(
            request, "segment_edit.html",
            {"segment": segment, "error": error, "form": form,
             "fields": FIELD_LABELS, "operators": OPERATOR_LABELS,
             "form_action": f"/segments/{segment.id}/edit",
             "submit_label": "Guardar alterações"},
            status_code=status,
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
            {"segment": segment, "members": members, "sizes": PAGE_SIZES,
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

    return app


def app_factory() -> FastAPI:
    return create_app(database_url=os.environ.get("MINI_MARKETO_DATABASE_URL"))
