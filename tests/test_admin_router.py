"""Standalone router integration using real repositories; no main.py mounting."""

import asyncio
from contextlib import contextmanager
from http.cookies import SimpleCookie
import re
from types import SimpleNamespace
from threading import get_ident
from urllib.parse import urlencode

import pytest
from fastapi import FastAPI

from mini_marketo import passwords
from mini_marketo.admin_router import create_access_router
from mini_marketo.repositories.access_repository import AccessRepository
from mini_marketo.services.login_attempt_limiter import LoginAttemptConfig
from mini_marketo.web_security import CsrfTokens, WebSecurityConfig

PASSWORD = "test-only-correct-password-2026"


@pytest.fixture(scope="session")
def password_hash():
    return passwords.hash_password(PASSWORD)


class Client:
    def __init__(self, app, web):
        self.app, self.web, self.cookies = app, web, {}
        self.now = 1000

    def request(self, method, target, data=None, headers=None):
        path, _, query = target.partition("?")
        body = urlencode(data).encode() if data is not None else b""
        values = {"host": "admin.example.test"}
        if data is not None:
            values.update({"content-type": "application/x-www-form-urlencoded", "origin": self.web.origin})
        if self.cookies:
            values["cookie"] = "; ".join(f"{k}={v}" for k, v in self.cookies.items())
        values.update(headers or {})
        scope = dict(type="http", asgi={"version": "3.0", "spec_version": "2.3"},
                     http_version="1.1", method=method, scheme="https", path=path,
                     raw_path=path.encode(), query_string=query.encode(), root_path="",
                     headers=[(k.encode(), v.encode()) for k, v in values.items()],
                     client=("192.0.2.1", 12345), server=("admin.example.test", 443))
        sent, read = [], False
        async def receive():
            nonlocal read
            if read:
                return {"type": "http.disconnect"}
            read = True
            return {"type": "http.request", "body": body, "more_body": False}
        async def send(message):
            sent.append(message)
        asyncio.run(self.app(scope, receive, send))
        start = next(m for m in sent if m["type"] == "http.response.start")
        response_headers = [(k.decode().lower(), v.decode()) for k, v in start["headers"]]
        for key, value in response_headers:
            if key == "set-cookie":
                parsed = SimpleCookie(); parsed.load(value)
                for name, cookie in parsed.items():
                    if cookie["max-age"] == "0":
                        self.cookies.pop(name, None)
                    else:
                        self.cookies[name] = cookie.value
        text = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body").decode()
        return SimpleNamespace(status=start["status"], text=text, headers=dict(response_headers), raw_headers=response_headers)

    def form(self):
        response = self.request("GET", "/login")
        assert response.status == 200
        return re.search(r'name="csrf_token" value="([^"]+)"', response.text).group(1)

    def login(self, *, username="operator", password=PASSWORD, target="/contacts"):
        return self.request("POST", "/login", {"username": username, "password": password,
            "csrf_token": self.form(), "next": target})


@pytest.fixture
def setup(db, password_hash):
    if db.kind == "sqlite":
        cls = AccessRepository
    else:
        from mini_marketo.repositories.postgres_access_repository import PostgresAccessRepository
        cls = PostgresAccessRepository
    @contextmanager
    def repos():
        handle = db.reopen()
        try:
            yield cls(handle.conn)
        finally:
            handle.close()
    web = WebSecurityConfig("https://admin.example.test", b"csrf-test-only-key-" * 2, 300, 120, 60)
    limits = LoginAttemptConfig(b"rate-test-only-key-" * 2, 60, 3, 20, 100)
    app = FastAPI()
    client = Client(app, web)
    app.include_router(create_access_router(repos, web_config=web, login_config=limits,
                                           max_form_bytes=4096, clock=lambda: client.now))
    with repos() as repo:
        user = repo.create_user("operator", password_hash, "admin", client.now)
    return SimpleNamespace(client=client, web=web, repos=repos, user=user, cls=cls)


def test_login_form_and_head_are_private(setup):
    c = setup.client
    response = c.request("GET", "/login?next=https://evil.example")
    assert response.status == 200 and 'value="/"' in response.text
    assert response.headers["cache-control"] == "no-store"
    cookies = " ".join(v for k, v in response.raw_headers if k == "set-cookie")
    assert "Secure" in cookies and "HttpOnly" in cookies and "SameSite=lax" in cookies
    assert c.request("HEAD", "/login").text == ""
    assert c.request("GET", "/logout").status == 405


@pytest.mark.parametrize("role", ["admin", "viewer"])
def test_login_issues_only_hashed_server_session(setup, db, role):
    with setup.repos() as repo:
        user = setup.user
        assert repo.update_user(user.id, user.credential_version, user.password_hash, role, True)
    response = setup.client.login()
    assert response.status == 303 and response.headers["location"] == "/contacts"
    token = setup.client.cookies[setup.web.session_cookie]
    assert setup.web.login_cookie not in setup.client.cookies
    digest = db.scalar("SELECT token_hash FROM admin_sessions")
    assert digest != token and len(digest) == 64


@pytest.mark.parametrize("change", [{"origin": "https://evil.example"}, {"origin": "null"}])
def test_bad_origin_does_not_reserve_or_issue(setup, db, change):
    c = setup.client
    response = c.request("POST", "/login", {"username": "operator", "password": PASSWORD,
        "csrf_token": c.form()}, headers=change)
    assert response.status == 403
    assert db.scalar("SELECT count(*) FROM admin_login_attempts") == 0
    assert db.scalar("SELECT count(*) FROM admin_sessions") == 0


@pytest.mark.parametrize("change", ["binding", "expired"])
def test_invalid_csrf_does_not_reserve(setup, db, change):
    c = setup.client
    token = c.form()
    if change == "binding":
        c.cookies[setup.web.login_cookie] = "a" * 43
    else:
        c.now += 60
    response = c.request("POST", "/login", {"username": "operator", "password": PASSWORD, "csrf_token": token})
    assert response.status == 403
    assert db.scalar("SELECT count(*) FROM admin_login_attempts") == 0


@pytest.mark.parametrize("kind", ["wrong_password", "unknown", "inactive"])
def test_credential_failures_are_generic(setup, db, kind):
    if kind == "inactive":
        with setup.repos() as repo:
            user = setup.user
            assert repo.update_user(user.id, user.credential_version, user.password_hash, user.role, False)
    response = setup.client.login(username="missing" if kind == "unknown" else "operator",
                                  password="wrong" if kind == "wrong_password" else PASSWORD)
    assert response.status == 401
    assert "Não foi possível iniciar sessão." in response.text
    assert PASSWORD not in response.text
    assert setup.web.session_cookie not in setup.client.cookies
    assert db.scalar("SELECT count(*) FROM admin_sessions") == 0


def test_limit_is_reserved_before_hashing(setup, db, monkeypatch):
    original, calls = passwords.verify_password, []
    event_loop_thread = get_ident()
    def verify(stored, supplied):
        calls.append(get_ident())
        return original(stored, supplied)
    monkeypatch.setattr(passwords, "verify_password", verify)
    for _ in range(3):
        assert setup.client.login(password="wrong").status == 401
    response = setup.client.login(password="wrong")
    assert response.status == 429 and response.headers["retry-after"] == "60"
    assert len(calls) == 3
    assert all(thread != event_loop_thread for thread in calls)
    assert db.scalar("SELECT count(*) FROM admin_sessions") == 0


def test_storage_failure_does_not_authenticate(setup, db, monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError("storage unavailable")
    monkeypatch.setattr(setup.cls, "reserve_login_attempts", fail)
    response = setup.client.login()
    assert response.status == 503 and "storage unavailable" not in response.text
    assert setup.web.session_cookie not in setup.client.cookies
    assert db.scalar("SELECT count(*) FROM admin_sessions") == 0


def test_relogin_rotates_session_and_denies_external_redirect(setup, db):
    c = setup.client
    assert c.login().status == 303
    old = c.cookies[setup.web.session_cookie]
    response = c.login(target="https://evil.example")
    assert response.status == 303 and response.headers["location"] == "/"
    assert c.cookies[setup.web.session_cookie] != old
    assert db.scalar("SELECT count(*) FROM admin_sessions") == 1


def test_logout_requires_its_csrf_purpose_and_revokes(setup, db):
    c = setup.client
    assert c.login().status == 303
    token = c.cookies[setup.web.session_cookie]
    csrf = CsrfTokens(setup.web)
    wrong = csrf.issue(token, "admin", now=c.now)
    assert c.request("POST", "/logout", {"csrf_token": wrong}).status == 403
    assert db.scalar("SELECT count(*) FROM admin_sessions") == 1
    valid = csrf.issue(token, "logout", now=c.now)
    assert c.request("POST", "/logout", {"csrf_token": valid}).status == 303
    assert db.scalar("SELECT count(*) FROM admin_sessions") == 0
    assert setup.web.session_cookie not in c.cookies


def test_expired_session_cannot_log_out_as_authenticated(setup):
    c = setup.client
    assert c.login().status == 303
    c.now += 121
    csrf = CsrfTokens(setup.web).issue(c.cookies[setup.web.session_cookie], "logout", now=c.now)
    assert c.request("POST", "/logout", {"csrf_token": csrf}).status == 401


def test_unknown_fields_and_large_body_do_not_reserve(setup, db):
    c = setup.client
    assert c.request("POST", "/login", {"csrf_token": c.form(), "role": "admin"}).status == 400
    assert c.login(password="x" * 5000).status == 413
    assert db.scalar("SELECT count(*) FROM admin_login_attempts") == 0


def test_duplicate_login_cookie_is_rejected_before_storage(setup, db):
    c = setup.client
    csrf = c.form()
    nonce = c.cookies[setup.web.login_cookie]
    cookie = f"{setup.web.login_cookie}={nonce}; {setup.web.login_cookie}={nonce}"
    response = c.request("POST", "/login", {"username": "operator", "password": PASSWORD,
                        "csrf_token": csrf}, headers={"cookie": cookie})
    assert response.status == 403
    assert db.scalar("SELECT count(*) FROM admin_login_attempts") == 0


def test_revoked_session_cannot_log_out_as_authenticated(setup, db):
    c = setup.client
    assert c.login().status == 303
    token = c.cookies[setup.web.session_cookie]
    csrf = CsrfTokens(setup.web).issue(token, "logout", now=c.now)
    with setup.repos() as repo:
        assert repo.revoke_user_sessions(setup.user.id) == 1
    assert c.request("POST", "/logout", {"csrf_token": csrf}).status == 401
    assert db.scalar("SELECT count(*) FROM admin_sessions") == 0


def test_redirect_value_is_escaped_in_the_form(setup):
    target = '/contacts?x="><script>alert(1)</script>'
    response = setup.client.request("GET", "/login?" + urlencode({"next": target}))
    assert response.status == 200
    assert "<script>" not in response.text
    assert "&lt;script&gt;" in response.text


def test_reused_csrf_and_limiter_keys_are_rejected():
    web = WebSecurityConfig("https://admin.example.test", b"test-only-shared-key-" * 2, 300, 120, 60)
    limits = LoginAttemptConfig(web.csrf_key, 60, 3, 20, 100)
    with pytest.raises(ValueError, match="independentes"):
        create_access_router(lambda: None, web_config=web, login_config=limits, max_form_bytes=4096)
