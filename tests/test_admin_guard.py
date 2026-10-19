import asyncio
from contextlib import contextmanager
from types import SimpleNamespace
from urllib.parse import urlencode


import pytest
from fastapi import FastAPI, Request


from mini_marketo.admin_guard import AdminAccessGuard
from mini_marketo.admin_router import create_access_router
from mini_marketo.repositories.access_repository import AccessRepository
from mini_marketo.services.login_attempt_limiter import LoginAttemptConfig
from mini_marketo.services.session_service import SessionService
from mini_marketo.web_security import CsrfTokens, WebSecurityConfig



class Client:
    def __init__(self, app):
        self.app = app


    def request(self, method, target, *, body=b"", headers=None):
        path, _, query = target.partition("?")
        values = {"host": "admin.example.test"}
        values.update(headers or {})
        if isinstance(body, dict):
            body = urlencode(body).encode()
            values.setdefault("content-type", "application/x-www-form-urlencoded")
        scope = dict(
            type="http",
            asgi={"version": "3.0", "spec_version": "2.3"},
            http_version="1.1",
            method=method,
            scheme="https",
            path=path,
            raw_path=path.encode(),
            query_string=query.encode(),
            root_path="",
            headers=[(key.lower().encode(), value.encode()) for key, value in values.items()],
            client=("192.0.2.1", 12345),
            server=("admin.example.test", 443),
        )
        sent = []
        read = False


        async def receive():
            nonlocal read
            if read:
                return {"type": "http.disconnect"}
            read = True
            return {"type": "http.request", "body": body, "more_body": False}


        async def send(message):
            sent.append(message)


        asyncio.run(self.app(scope, receive, send))
        start = next(item for item in sent if item["type"] == "http.response.start")
        response_headers = [(key.decode().lower(), value.decode()) for key, value in start["headers"]]
        response_body = b"".join(
            item.get("body", b"") for item in sent if item["type"] == "http.response.body"
        )
        return SimpleNamespace(
            status=start["status"],
            body=response_body,
            headers=dict(response_headers),
        )



@pytest.fixture
def guarded(db):
    if db.kind == "sqlite":
        repo_class = AccessRepository
    else:
        from mini_marketo.repositories.postgres_access_repository import PostgresAccessRepository


        repo_class = PostgresAccessRepository


    @contextmanager
    def repos():
        handle = db.reopen()
        try:
            yield repo_class(handle.conn)
        finally:
            handle.close()


    web = WebSecurityConfig(
        "https://admin.example.test", b"guard-csrf-test-key-" * 2, 100, 40, 60
    )
    app = FastAPI()
    seen = []


    @app.get("/contacts")
    async def list_contacts():
        seen.append(("read", None))
        return {"ok": True}


    @app.get("/segments/123/edit")
    async def edit_segment():
        seen.append(("edit", None))
        return {"ok": True}


    @app.post("/contacts")
    async def create_contact(request: Request):
        body = await request.body()
        seen.append(("write", body))
        return {"body": body.decode()}


    @app.post("/subscribe")
    async def subscribe(request: Request):
        body = await request.body()
        seen.append(("public", body))
        return {"ok": True}


    with repos() as repo:
        admin = repo.create_user("operator", "test-hash", "admin", 1000)
        viewer = repo.create_user("reader", "test-hash", "viewer", 1000)


    def issue(user):
        with repos() as repo:
            issued = SessionService(repo, absolute_seconds=100, idle_seconds=40).issue(
                user.id, user.credential_version, now=1000
            )
        assert issued is not None
        return issued.token


    app.include_router(create_access_router(
        repos,
        web_config=web,
        login_config=LoginAttemptConfig(b"guard-limit-test-key-" * 2, 60, 3, 10, 20),
        max_form_bytes=4096,
        clock=lambda: 1001,
    ))
    guarded_app = AdminAccessGuard(
        app,
        repo_factory=repos,
        web_config=web,
        max_body_bytes=8192,
        max_form_fields=100,
        clock=lambda: 1001,
    )
    return SimpleNamespace(
        client=Client(guarded_app),
        web=web,
        admin_token=issue(admin),
        viewer_token=issue(viewer),
        admin=admin,
        repos=repos,
        seen=seen,
        db=db,
    )



def authenticated_headers(guarded, token):
    return {"cookie": f"{guarded.web.session_cookie}={token}"}



def test_valid_session_reaches_read_handler(guarded):
    response = guarded.client.request(
        "GET", "/contacts", headers=authenticated_headers(guarded, guarded.admin_token)
    )
    assert response.status == 200
    assert guarded.seen == [("read", None)]



def test_viewer_cannot_reach_admin_only_edit_or_write_handlers(guarded):
    headers = authenticated_headers(guarded, guarded.viewer_token)
    csrf = CsrfTokens(guarded.web).issue(guarded.viewer_token, "admin", now=1000)
    response = guarded.client.request("GET", "/segments/123/edit", headers=headers)
    assert response.status == 403
    response = guarded.client.request(
        "POST",
        "/contacts",
        body={"csrf_token": csrf},
        headers={**headers, "origin": guarded.web.origin},
    )
    assert response.status == 403
    assert guarded.seen == []



@pytest.mark.parametrize(
    ("target", "expected", "authenticated"),
    [("/contacts", 401, False), ("/new-admin-route", 404, True), ("/subscribe/new", 404, True)],
)
def test_invalid_or_unclassified_requests_do_not_reach_handlers(
    guarded, target, expected, authenticated
):
    headers = authenticated_headers(guarded, guarded.admin_token) if authenticated else {}
    response = guarded.client.request("GET", target, headers=headers)
    assert response.status == expected
    assert guarded.seen == []



@pytest.mark.parametrize(
    ("change", "expected"),
    [("origin", 403), ("csrf", 403), ("too_large", 413)],
)
def test_denied_mutations_have_no_business_effects(guarded, change, expected):
    csrf = CsrfTokens(guarded.web).issue(guarded.admin_token, "admin", now=1000)
    headers = {
        **authenticated_headers(guarded, guarded.admin_token),
        "content-type": "application/x-www-form-urlencoded",
        "origin": "https://attacker.example.test" if change == "origin" else guarded.web.origin,
    }
    body = urlencode({"csrf_token": "invalid" if change == "csrf" else csrf}).encode()
    if change == "too_large":
        body += b"&unused=" + b"x" * 9000
    response = guarded.client.request("POST", "/contacts", body=body, headers=headers)
    assert response.status == expected
    assert guarded.seen == []



def test_non_bytes_body_chunk_is_rejected_without_business_effect(guarded):
    response = guarded.client.request(
        "POST",
        "/contacts",
        body="not bytes",
        headers={
            **authenticated_headers(guarded, guarded.admin_token),
            "content-type": "application/x-www-form-urlencoded",
            "origin": guarded.web.origin,
        },
    )
    assert response.status == 400
    assert guarded.seen == []



def test_valid_mutation_replays_exact_body_to_handler(guarded):
    csrf = CsrfTokens(guarded.web).issue(guarded.admin_token, "admin", now=1000)
    body = urlencode([
        ("name", "Example"),
        ("criterion_field", "email"),
        ("criterion_field", "company"),
        ("csrf_token", csrf),
    ]).encode()
    response = guarded.client.request(
        "POST",
        "/contacts",
        body=body,
        headers={
            **authenticated_headers(guarded, guarded.admin_token),
            "content-type": "application/x-www-form-urlencoded",
            "origin": guarded.web.origin,
        },
    )
    assert response.status == 200
    assert guarded.seen == [("write", body)]



def test_public_route_keeps_its_own_validation_without_admin_session(guarded):
    response = guarded.client.request("POST", "/subscribe", body=b"email=test%40example.test")
    assert response.status == 200
    assert guarded.seen == [("public", b"email=test%40example.test")]



def test_credential_change_invalidates_session_before_business_handler(guarded):
    with guarded.repos() as repo:
        assert repo.update_user(
            guarded.admin.id,
            guarded.admin.credential_version,
            "replacement-hash",
            guarded.admin.role,
            True,
        )
    response = guarded.client.request(
        "GET", "/contacts", headers=authenticated_headers(guarded, guarded.admin_token)
    )
    assert response.status == 401
    assert guarded.seen == []



@pytest.mark.parametrize("path", ["/login", "/logout"])
def test_access_router_keeps_ownership_of_origin_and_form_body(guarded, path):
    response = guarded.client.request(
        "POST",
        path,
        body=b"username=operator",
        headers={
            "origin": guarded.web.origin,
            "content-type": "application/json",
        },
    )
    assert response.status == 415
    assert guarded.seen == []



def test_duplicate_session_cookie_is_rejected(guarded):
    name = guarded.web.session_cookie
    response = guarded.client.request(
        "GET",
        "/contacts",
        headers={"cookie": f"{name}={guarded.admin_token}; {name}={guarded.admin_token}"},
    )
    assert response.status == 401
    assert guarded.seen == []



def test_storage_failure_fails_closed_without_logging_exception(caplog, guarded):
    marker = "sensitive-storage-marker"


    def fail():
        raise OSError(marker)


    app = AdminAccessGuard(
        guarded.client.app.app,
        repo_factory=fail,
        web_config=guarded.web,
        max_body_bytes=8192,
        max_form_fields=100,
        clock=lambda: 1001,
    )
    response = Client(app).request(
        "GET", "/contacts", headers=authenticated_headers(guarded, guarded.admin_token)
    )
    assert response.status == 503
    assert marker.encode() not in response.body
    assert marker not in caplog.text
    assert guarded.seen == []



def test_clock_failure_fails_closed_without_logging_exception(caplog, guarded):
    marker = "sensitive-clock-marker"


    def fail():
        raise RuntimeError(marker)


    app = AdminAccessGuard(
        guarded.client.app.app,
        repo_factory=guarded.repos,
        web_config=guarded.web,
        max_body_bytes=8192,
        max_form_fields=100,
        clock=fail,
    )
    response = Client(app).request(
        "GET", "/contacts", headers=authenticated_headers(guarded, guarded.admin_token)
    )
    assert response.status == 503
    assert marker.encode() not in response.body
    assert marker not in caplog.text
    assert guarded.seen == []
