from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.routing import APIRoute
from fastapi.templating import Jinja2Templates

from mini_marketo.admin_router import create_access_router
from mini_marketo.services.login_attempt_limiter import LoginAttemptConfig
from mini_marketo.web_security import CsrfTokens, WebSecurityConfig


TEMPLATES = Path(__file__).resolve().parents[1] / "src/mini_marketo/templates"
SESSION_TOKEN = "s" * 43
PASSWORD = "test-only-password-not-for-html"
CSRF_KEY = b"template-tests-csrf-key-32-bytes!"


class FormsParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.forms = []
        self.links = []
        self._form = None

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "form":
            self._form = {"attributes": attributes, "inputs": []}
            self.forms.append(self._form)
        elif tag in ("input", "select", "textarea") and self._form is not None:
            self._form["inputs"].append(attributes)
        elif tag == "a":
            self.links.append(attributes)

    def handle_endtag(self, tag):
        if tag == "form":
            self._form = None


@pytest.fixture
def setup():
    web = WebSecurityConfig(
        "https://admin.example.test", CSRF_KEY, 300, 120, 60
    )
    csrf = CsrfTokens(web)
    admin_token = csrf.issue(SESSION_TOKEN, "admin", now=1000)
    logout_token = csrf.issue(SESSION_TOKEN, "logout", now=1000)
    return SimpleNamespace(
        templates=Jinja2Templates(directory=str(TEMPLATES)),
        web=web,
        csrf=csrf,
        admin_token=admin_token,
        logout_token=logout_token,
    )


def admin_context(setup, role="admin"):
    return {
        "role": role,
        "admin_csrf_token": setup.admin_token,
        "logout_csrf_token": setup.logout_token,
    }


def contexts():
    contact = SimpleNamespace(
        id=7,
        first_name="<script>private-name</script>",
        last_name="Example",
        emails=["one@example.test", "two@example.test"],
        company_name="<b>Company</b>",
        created_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
        email="one@example.test",
    )
    criterion_form = {
        "name": '<img src=x onerror="alert(1)">',
        "description": "",
        "match_mode": "all",
        "rows": [
            {"field": "", "operator": "is", "value": ""},
            {"field": "", "operator": "is", "value": ""},
        ],
    }
    segment = SimpleNamespace(
        id=8,
        name="<script>segment-name</script>",
        description="",
        match_mode="all",
        criteria=[],
    )
    campaign = SimpleNamespace(
        id=9,
        name="<script>campaign-name</script>",
        subject="Subject",
        segment_name="Segment",
        segment_id=8,
        purpose="product",
        status="draft",
    )
    return {
        "contacts.html": {
            "contacts": [contact],
            "companies": [],
            "error": None,
            "form": {"first_name": "", "last_name": "", "email": "", "company_id": ""},
        },
        "consent.html": {
            "contact": contact,
            "rows": [],
            "history": [],
            "sources": {},
            "purposes": {},
            "decisions": {},
            "error": None,
        },
        "segments.html": {
            "segments": [],
            "error": None,
            "form": criterion_form,
            "fields": {},
            "operators": {},
            "form_action": "/segments",
            "submit_label": "Guardar",
        },
        "segment_edit.html": {
            "segment": segment,
            "error": None,
            "form": criterion_form,
            "fields": {},
            "operators": {},
            "form_action": "/segments/8/edit",
            "submit_label": "Guardar alterações",
        },
        "segment.html": {
            "segment": segment,
            "members": SimpleNamespace(
                total=0, items=[], pages=1, first=0, last=0, per_page=20, page=1,
                has_previous=False, has_next=False,
            ),
            "sizes": [20],
            "fields": {},
            "operators": {},
        },
        "campaigns.html": {
            "campaigns": [],
            "segments": [],
            "purposes": {},
            "statuses": {},
            "error": None,
            "form": {
                "name": "", "subject": "", "segment_id": "", "purpose": "",
                "body_text": "", "body_html": "",
            },
        },
        "campaign.html": {
            "campaign": campaign,
            "purposes": {"product": "Produto"},
            "statuses": {"draft": "Rascunho"},
            "message_statuses": {},
            "skip_reasons": {},
            "counts": {},
            "problems": [],
            "error": None,
        },
        "suppressions.html": {
            "suppressions": [
                SimpleNamespace(
                    created_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
                    email="suppressed@example.test",
                    reason="manual",
                    source="admin",
                    detail="",
                )
            ],
            "reasons": {"manual": "Manual"},
            "sources": {"admin": "Admin"},
            "removable": {"manual"},
            "error": None,
            "form": {"email": "", "detail": ""},
        },
    }


def render_admin_pages(setup, access_context_marker=True, role="admin"):
    rendered = {}
    for name, context in contexts().items():
        if access_context_marker:
            context["admin_access"] = admin_context(setup, role)
        context["request"] = SimpleNamespace(url=SimpleNamespace(path="/"))
        rendered[name] = setup.templates.get_template(name).render(**context)
    return rendered


def parse_forms(html):
    parser = FormsParser()
    parser.feed(html)
    return parser


def test_admin_mutation_forms_each_have_one_valid_admin_csrf_field(setup):
    rendered = render_admin_pages(setup)
    forms_seen = 0
    for html in rendered.values():
        parser = parse_forms(html)
        for form in parser.forms:
            attrs = form["attributes"]
            if attrs.get("method", "get").lower() != "post":
                continue
            fields = [
                field for field in form["inputs"]
                if field.get("type") == "hidden" and field.get("name") == "csrf_token"
            ]
            assert len(fields) == 1, attrs
            if attrs.get("action") == "/logout":
                assert fields[0]["value"] == setup.logout_token
                purpose = "logout"
            else:
                assert fields[0]["value"] == setup.admin_token
                purpose = "admin"
            assert setup.csrf.verify(fields[0]["value"], SESSION_TOKEN, purpose, now=1000)
            forms_seen += 1
    assert forms_seen >= 12


def test_logout_is_only_a_post_form_with_separate_logout_csrf(setup):
    html = render_admin_pages(setup)["contacts.html"]
    parser = parse_forms(html)
    logout_forms = [
        form for form in parser.forms
        if form["attributes"].get("action") == "/logout"
    ]
    assert len(logout_forms) == 1
    logout = logout_forms[0]
    assert logout["attributes"]["method"].lower() == "post"
    csrf_fields = [
        field for field in logout["inputs"]
        if field.get("type") == "hidden" and field.get("name") == "csrf_token"
    ]
    assert len(csrf_fields) == 1
    assert csrf_fields[0]["value"] == setup.logout_token
    assert setup.csrf.verify(csrf_fields[0]["value"], SESSION_TOKEN, "logout", now=1000)
    assert not setup.csrf.verify(csrf_fields[0]["value"], SESSION_TOKEN, "admin", now=1000)
    assert not any(link.get("href") == "/logout" for link in parser.links)


def test_access_router_exposes_logout_only_as_post(setup):
    router = create_access_router(
        lambda: None,
        web_config=setup.web,
        login_config=LoginAttemptConfig(b"independent-limit-test-key-32bytes", 60, 3, 10, 20),
        max_form_bytes=4096,
        clock=lambda: 1000,
    )
    logout_routes = [
        route for route in router.routes
        if isinstance(route, APIRoute) and route.path == "/logout"
    ]
    assert len(logout_routes) == 1
    assert logout_routes[0].methods == {"POST"}


def test_viewer_has_read_only_controls_and_post_logout(setup):
    rendered = render_admin_pages(setup, role="viewer")
    for html in rendered.values():
        parser = parse_forms(html)
        post_forms = [
            form for form in parser.forms
            if form["attributes"].get("method", "get").lower() == "post"
        ]
        assert all(form["attributes"].get("action") == "/logout" for form in post_forms)
        assert not any(link.get("href", "").endswith("/edit") for link in parser.links)
    logout = parse_forms(rendered["contacts.html"]).forms[-1]
    csrf_fields = [
        field for field in logout["inputs"]
        if field.get("type") == "hidden" and field.get("name") == "csrf_token"
    ]
    assert len(csrf_fields) == 1
    assert setup.csrf.verify(csrf_fields[0]["value"], SESSION_TOKEN, "logout", now=1000)


def test_missing_auth_context_hides_admin_forms_and_logout(setup):
    rendered = render_admin_pages(setup, access_context_marker=False)
    for html in rendered.values():
        parser = parse_forms(html)
        assert not any(
            form["attributes"].get("method", "get").lower() == "post"
            for form in parser.forms
        )
        assert not any(link.get("href") == "/logout" for link in parser.links)


def test_html_escapes_data_and_contains_no_session_password_or_secret(setup):
    html = "\n".join(render_admin_pages(setup).values())
    assert "<script>private-name</script>" not in html
    assert "<script>segment-name</script>" not in html
    assert "<script>campaign-name</script>" not in html
    assert "<img src=x onerror=" not in html
    assert SESSION_TOKEN not in html
    assert PASSWORD not in html
    assert CSRF_KEY.decode() not in html


def test_public_forms_keep_their_own_fields_and_no_admin_csrf(setup):
    templates = setup.templates
    subscribe = templates.get_template("subscribe.html").render(
        lang="pt",
        t=lambda key, **params: key,
        purposes=["newsletter", "product"],
        privacy_url="",
        error=False,
        form={"first_name": "", "last_name": "", "email": "", "purposes": []},
    )
    subscribe_parser = parse_forms(subscribe)
    assert len(subscribe_parser.forms) == 1
    assert [
        field.get("name") for field in subscribe_parser.forms[0]["inputs"]
    ].count("purposes") == 2
    assert not any(
        field.get("name") == "csrf_token"
        for field in subscribe_parser.forms[0]["inputs"]
    )

    notice = templates.get_template("subscribe_notice.html").render(
        lang="pt", title="Confirmar", body="Continuar", action="/subscribe/confirm/public-token",
        button="Confirmar", t=lambda key, **params: key,
    )
    notice_parser = parse_forms(notice)
    assert len(notice_parser.forms) == 1
    assert notice_parser.forms[0]["attributes"] == {
        "method": "post", "action": "/subscribe/confirm/public-token"
    }
    assert not any(
        field.get("name") == "csrf_token"
        for field in notice_parser.forms[0]["inputs"]
    )

    unsubscribe = templates.get_template("unsubscribe.html").render(
        view=SimpleNamespace(masked_email="m***@example.test", purpose="product",
                             already_withdrawn=False),
        token="public-unsubscribe-token",
        done=False,
        purposes={"product": "Produto"},
    )
    unsubscribe_parser = parse_forms(unsubscribe)
    assert len(unsubscribe_parser.forms) == 1
    assert unsubscribe_parser.forms[0]["attributes"] == {
        "method": "post",
        "action": "/unsubscribe/public-unsubscribe-token",
    }
    assert not any(
        field.get("name") == "csrf_token"
        for field in unsubscribe_parser.forms[0]["inputs"]
    )


def test_admin_segment_form_keeps_repeated_business_fields(setup):
    html = render_admin_pages(setup)["segments.html"]
    segment_form = next(
        form for form in parse_forms(html).forms
        if form["attributes"].get("action") == "/segments"
    )
    names = [field.get("name") for field in segment_form["inputs"]]
    assert names.count("criterion_field") == 2
    assert names.count("criterion_operator") == 2
    assert names.count("criterion_value") == 2
