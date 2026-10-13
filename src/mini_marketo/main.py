import os
from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .database import connect, init_db
from .domain.contact import ValidationError
from .repositories.contact_repository import ContactRepository
from .services.contact_service import ContactService

BASE = Path(__file__).parent
DEFAULT_DB = Path(os.environ.get("MINI_MARKETO_DB", BASE.parent.parent / "data" / "mini_marketo.db"))


def create_app(db_path: str | Path = DEFAULT_DB) -> FastAPI:
    conn = connect(db_path)
    init_db(conn)
    service = ContactService(ContactRepository(conn))
    templates = Jinja2Templates(directory=str(BASE / "templates"))

    app = FastAPI(title="Mini-Marketo")
    app.mount("/static", StaticFiles(directory=str(BASE / "static")), name="static")

    def page(request: Request, error=None, form=None, status=200):
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
    def contacts(request: Request):
        return page(request)

    @app.post("/contacts")
    def create_contact(request: Request, first_name: str = Form(""), last_name: str = Form(""),
                       email: str = Form(""), company_id: str = Form("")):
        form = {"first_name": first_name, "last_name": last_name,
                "email": email, "company_id": company_id}
        try:
            cid = int(company_id) if company_id.strip() else None
            service.create_contact(first_name, last_name, email, cid)
        except ValueError as exc:  # ValidationError herda de ValueError
            msg = str(exc) if isinstance(exc, ValidationError) else "Empresa inválida."
            return page(request, msg, form, 422)
        return RedirectResponse("/contacts", status_code=303)

    @app.post("/companies")
    def create_company(request: Request, name: str = Form(""), website: str = Form("")):
        try:
            service.create_company(name, website)
        except ValidationError as exc:
            return page(request, str(exc), None, 422)
        return RedirectResponse("/contacts", status_code=303)

    return app


def app_factory() -> FastAPI:
    return create_app()
