import hashlib
import re
import time
from contextlib import contextmanager
from dataclasses import replace
from types import SimpleNamespace
from urllib.parse import urlencode

import pytest
from fastapi.testclient import TestClient

from mini_marketo import passwords
from mini_marketo.main import (
    ADMIN_BODY_MAX_BYTES,
    ADMIN_BODY_MAX_FIELDS,
    build_access_repository_factory,
    create_app as production_create_app,
)
from mini_marketo.repositories.access_repository import AccessRepository
from mini_marketo.services.session_service import SessionService
from mini_marketo.web_security import CsrfTokens, WebSecurityConfig
from admin_app_support import TEST_LOGIN_CONFIG, TEST_WEB_CONFIG
from test_subscription import make as make_subscription


PASSWORD = "Test-only-password-2026!"


@pytest.fixture(scope="session")
def password_hash():
    return passwords.hash_password(PASSWORD)


@pytest.fixture
def integrated(db, password_hash):
    if db.kind == "sqlite":
        from mini_marketo.repositories.access_repository import AccessRepository as Repo
    else:
        from mini_marketo.repositories.postgres_access_repository import (
            PostgresAccessRepository as Repo,
        )

    @contextmanager
    def repos():
        handle = db.reopen()
        try:
            yield Repo(handle.conn)
        finally:
            handle.close()

    subscriptions, _, _ = make_subscription(db)
    app = production_create_app(
        service=db.service(),
        segment_service=db.segment_service(),
        consent_service=db.consent_service(),
        subscription_service=subscriptions,
        access_repo_factory=repos,
        web_security_config=TEST_WEB_CONFIG,
        login_attempt_config=TEST_LOGIN_CONFIG,
        events_token="",
    )
    with repos() as repo:
        admin = repo.create_user("admin", password_hash, "admin", int(time.time()))
        viewer = repo.create_user("viewer", password_hash, "viewer", int(time.time()))
    return SimpleNamespace(
        app=app, db=db, repos=repos, admin=admin, viewer=viewer
    )


def client(app):
    return TestClient(
        app,
        base_url=TEST_WEB_CONFIG.origin,
        raise_server_exceptions=False,
        client=("192.0.2.1", 12345),
    )


def login(test_app, username="admin"):
    c = client(test_app.app)
    page = c.get("/login")
    csrf = re.search(r'name="csrf_token" value="([^"]+)"', page.text).group(1)
    response = c.post(
        "/login",
        data={"username": username, "password": PASSWORD, "csrf_token": csrf},
        headers={"origin": TEST_WEB_CONFIG.origin},
        follow_redirects=False,
    )
    assert response.status_code == 303
    return c


def csrf_for(client, purpose="admin"):
    token = client.cookies.get(TEST_WEB_CONFIG.session_cookie)
    return CsrfTokens(TEST_WEB_CONFIG).issue(token, purpose, now=int(time.time()))


def post_fields(client, path, fields, *, headers, **kwargs):
    return client.post(
        path,
        content=urlencode(fields),
        headers={"content-type": "application/x-www-form-urlencoded", **headers},
        **kwargs,
    )


def test_login_read_mutation_logout_and_private_headers(integrated):
    c = login(integrated)
    page = c.get("/contacts")
    assert page.status_code == 200
    assert page.headers["cache-control"] == "no-store"
    assert page.headers["referrer-policy"] == "no-referrer"
    assert page.headers["x-content-type-options"] == "nosniff"
    assert page.headers["x-frame-options"] == "DENY"
    assert c.cookies.get(TEST_WEB_CONFIG.session_cookie) not in page.text
    assert TEST_WEB_CONFIG.csrf_key.decode() not in page.text
    assert "hmac_key" not in page.text and PASSWORD not in page.text
    admin_csrf = re.search(
        r'<form method="post" action="/companies".*?>\s*'
        r'<input type="hidden" name="csrf_token" value="([^"]+)"',
        page.text,
        re.S,
    ).group(1)
    assert CsrfTokens(TEST_WEB_CONFIG).verify(
        admin_csrf,
        c.cookies.get(TEST_WEB_CONFIG.session_cookie),
        "admin",
        now=int(time.time()),
    )
    response = c.post(
        "/companies",
        data={"name": "IntegrationCo", "website": "", "csrf_token": csrf_for(c)},
        headers={"origin": TEST_WEB_CONFIG.origin},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["cache-control"] == "no-store"
    assert integrated.db.scalar("SELECT count(*) FROM companies") == 1

    logout_token = re.search(
        r'<form method="post" action="/logout".*?>\s*'
        r'<input type="hidden" name="csrf_token" value="([^"]+)"',
        page.text,
        re.S,
    ).group(1)
    response = c.post(
        "/logout",
        data={"csrf_token": logout_token},
        headers={"origin": TEST_WEB_CONFIG.origin},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/login"
    assert response.headers["cache-control"] == "no-store"
    assert c.get("/contacts").status_code == 401


def test_viewer_can_read_but_direct_mutation_is_denied(integrated):
    c = login(integrated, "viewer")
    page = c.get("/contacts")
    assert page.status_code == 200
    assert 'action="/companies"' not in page.text
    response = c.post(
        "/companies",
        data={"name": "MustNotExist", "csrf_token": csrf_for(c)},
        headers={"origin": TEST_WEB_CONFIG.origin},
    )
    assert response.status_code == 403
    assert integrated.db.scalar(
        "SELECT count(*) FROM companies WHERE name = " + integrated.db.p,
        ("MustNotExist",),
    ) == 0
    logout_token = re.search(
        r'<form method="post" action="/logout".*?>\s*'
        r'<input type="hidden" name="csrf_token" value="([^"]+)"',
        page.text,
        re.S,
    ).group(1)
    assert c.post(
        "/logout",
        data={"csrf_token": logout_token},
        headers={"origin": TEST_WEB_CONFIG.origin},
        follow_redirects=False,
    ).status_code == 303


def test_origin_csrf_and_duplicate_cookie_fail_before_business_operations(integrated):
    c = login(integrated)
    valid = csrf_for(c)
    cases = (
        ({"origin": "https://evil.example"}, {"csrf_token": valid}),
        ({}, {"csrf_token": valid}),
        ({"origin": TEST_WEB_CONFIG.origin}, {}),
        ({"origin": TEST_WEB_CONFIG.origin}, {"csrf_token": "invalid"}),
        (
            {"origin": TEST_WEB_CONFIG.origin},
            [("csrf_token", valid), ("csrf_token", valid)],
        ),
        (
            {"origin": TEST_WEB_CONFIG.origin},
            {"csrf_token": csrf_for(c, "logout")},
        ),
    )
    for headers, data in cases:
        response = (
            post_fields(c, "/companies", data, headers=headers)
            if isinstance(data, list)
            else c.post("/companies", data=data, headers=headers)
        )
        assert response.status_code == 403
    assert integrated.db.scalar("SELECT count(*) FROM companies") == 0

    token = c.cookies.get(TEST_WEB_CONFIG.session_cookie)
    duplicate = client(integrated.app).get(
        "/contacts",
        headers={"cookie": f"{TEST_WEB_CONFIG.session_cookie}={token}; "
                           f"{TEST_WEB_CONFIG.session_cookie}={token}"},
    )
    assert duplicate.status_code == 401


def test_logout_rejects_admin_purpose_token_and_get_is_unclassified(integrated):
    c = login(integrated)
    response = c.post(
        "/logout",
        data={"csrf_token": csrf_for(c, "admin")},
        headers={"origin": TEST_WEB_CONFIG.origin},
    )
    assert response.status_code == 403
    assert c.get("/contacts").status_code == 200
    assert c.get("/logout").status_code == 404


def test_admin_form_context_survives_validation_errors_and_replays_repeated_fields(integrated):
    c = login(integrated)
    page = c.get("/contacts")
    admin_token = re.search(
        r'<form method="post" action="/contacts".*?>\s*'
        r'<input type="hidden" name="csrf_token" value="([^"]+)"',
        page.text,
        re.S,
    ).group(1)
    session_token = c.cookies.get(TEST_WEB_CONFIG.session_cookie)
    assert admin_token in page.text and session_token not in page.text
    assert TEST_WEB_CONFIG.csrf_key.decode() not in page.text
    invalid = c.post(
        "/contacts",
        data={"first_name": "", "last_name": "", "email": "invalid",
              "csrf_token": csrf_for(c)},
        headers={"origin": TEST_WEB_CONFIG.origin},
    )
    assert invalid.status_code == 422
    error_token = re.search(
        r'<form method="post" action="/contacts".*?>\s*'
        r'<input type="hidden" name="csrf_token" value="([^"]+)"',
        invalid.text,
        re.S,
    ).group(1)
    assert invalid.headers["cache-control"] == "no-store"
    assert session_token not in invalid.text
    assert CsrfTokens(TEST_WEB_CONFIG).verify(
        error_token, session_token, "admin", now=int(time.time())
    )

    fields = [("name", "Repeated"), ("description", ""), ("match_mode", "all")]
    for index in range(10):
        fields.extend((
            ("criterion_field", "company" if index == 0 else ""),
            ("criterion_operator", "is"),
            ("criterion_value", "IntegrationCo" if index == 0 else ""),
        ))
    fields.append(("csrf_token", csrf_for(c)))
    response = post_fields(
        c,
        "/segments",
        fields,
        headers={"origin": TEST_WEB_CONFIG.origin},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert len(integrated.db.segment_service().list_segments()[0].criteria) == 1


def test_body_and_field_limits_are_enforced(integrated):
    c = login(integrated)
    token = csrf_for(c)
    too_large = f"csrf_token={token}&x=" + "a" * ADMIN_BODY_MAX_BYTES
    response = c.post(
        "/companies",
        content=too_large,
        headers={
            "content-type": "application/x-www-form-urlencoded",
            "origin": TEST_WEB_CONFIG.origin,
        },
    )
    assert response.status_code == 413
    fields = [("csrf_token", token)] + [
        (f"x{index}", "x") for index in range(ADMIN_BODY_MAX_FIELDS)
    ]
    response = post_fields(
        c,
        "/companies",
        fields,
        headers={"origin": TEST_WEB_CONFIG.origin},
    )
    assert response.status_code == 403
    assert integrated.db.scalar("SELECT count(*) FROM companies") == 0


@pytest.mark.parametrize("state", ["revoked", "expired", "inactive", "stale_version"])
def test_invalid_persisted_sessions_are_denied(integrated, state):
    c = login(integrated)
    token = c.cookies.get(TEST_WEB_CONFIG.session_cookie)
    token_hash = hashlib.sha256(token.encode("ascii")).hexdigest()
    with integrated.repos() as repo:
        user = repo.get_by_username("admin")
        if state == "revoked":
            assert repo.revoke_session(token_hash)
        elif state == "inactive":
            assert repo.update_user(
                user.id, user.credential_version, user.password_hash, user.role, False
            )
        elif state == "stale_version":
            p = integrated.db.p
            integrated.db.execute(
                f"UPDATE admin_sessions SET credential_version = credential_version + 1 "
                f"WHERE token_hash = {p}",
                (token_hash,),
            )
        else:
            now = int(time.time())
            p = integrated.db.p
            integrated.db.execute(
                f"UPDATE admin_sessions SET created_at = {p}, last_seen_at = {p}, "
                f"expires_at = {p} WHERE token_hash = {p}",
                (now - 100, now - 100, now - 1, token_hash),
            )
    assert c.get("/contacts").status_code == 401


def test_unknown_role_is_denied_even_if_repository_returns_it(integrated, monkeypatch):
    c = login(integrated)
    if integrated.db.kind == "sqlite":
        cls = AccessRepository
    else:
        from mini_marketo.repositories.postgres_access_repository import (
            PostgresAccessRepository,
        )
        cls = PostgresAccessRepository
    original = cls.get_user

    def unknown_role(repo, user_id):
        user = original(repo, user_id)
        return replace(user, role="unknown") if user else None

    monkeypatch.setattr(cls, "get_user", unknown_role)
    assert c.get("/contacts").status_code == 401


def test_public_routes_remain_public_and_documentation_remains_protected(integrated):
    c = client(integrated.app)
    assert c.get("/subscribe").status_code == 200
    invalid = c.post(
        "/subscribe",
        data={"email": "invalid", "purposes": []},
        follow_redirects=False,
    )
    assert invalid.status_code == 422
    assert 'name="csrf_token"' not in invalid.text
    assert 'action="/logout"' not in c.get("/subscribe").text
    assert c.get("/static/style.css").status_code == 200
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert c.get(path).status_code == 401
    assert c.get("/not-classified").status_code == 404


def test_access_failures_fail_closed_without_exposing_exceptions(
    integrated, caplog, monkeypatch
):
    marker = "SENSITIVE-PERSISTENCE-MARKER"
    session_token = login(integrated).cookies.get(TEST_WEB_CONFIG.session_cookie)

    def client_with_session(app):
        result = client(app)
        result.cookies.set(
            TEST_WEB_CONFIG.session_cookie,
            session_token,
            domain="admin.example.test",
            path="/",
        )
        return result

    @contextmanager
    def broken_repository():
        raise RuntimeError(marker)
        yield

    app = production_create_app(
        service=integrated.db.service(),
        access_repo_factory=broken_repository,
        web_security_config=TEST_WEB_CONFIG,
        login_attempt_config=TEST_LOGIN_CONFIG,
        events_token="",
    )
    failed_client = client_with_session(app)
    response = failed_client.post(
        "/companies",
        data={"name": "MustNotPersist", "csrf_token": csrf_for(failed_client)},
        headers={"origin": TEST_WEB_CONFIG.origin},
    )
    assert response.status_code == 503
    assert marker not in response.text
    assert marker not in caplog.text
    assert integrated.db.scalar("SELECT count(*) FROM companies") == 0

    def broken_clock():
        raise RuntimeError(marker)

    app = production_create_app(
        service=integrated.db.service(),
        access_repo_factory=integrated.repos,
        web_security_config=TEST_WEB_CONFIG,
        login_attempt_config=TEST_LOGIN_CONFIG,
        access_clock=broken_clock,
        events_token="",
    )
    response = client_with_session(app).get("/contacts")
    assert response.status_code == 503
    assert marker not in response.text
    assert marker not in caplog.text

    valid_client = login(integrated)

    def broken_issue(*args, **kwargs):
        raise RuntimeError(marker)

    monkeypatch.setattr(CsrfTokens, "issue", broken_issue)
    response = valid_client.get("/contacts")
    assert response.status_code == 503
    assert marker not in response.text
    assert marker not in caplog.text

    monkeypatch.setattr(CsrfTokens, "issue", lambda *args, **kwargs: "")
    response = valid_client.get("/contacts")
    assert response.status_code == 503
    assert marker not in response.text
    assert marker not in caplog.text


def test_missing_or_invalid_production_security_configuration_fails_startup(monkeypatch):
    names = (
        "MINI_MARKETO_ADMIN_ORIGIN",
        "MINI_MARKETO_ADMIN_CSRF_KEY",
        "MINI_MARKETO_ADMIN_SESSION_ABSOLUTE_SECONDS",
        "MINI_MARKETO_ADMIN_SESSION_IDLE_SECONDS",
        "MINI_MARKETO_ADMIN_CSRF_SECONDS",
        "MINI_MARKETO_LOGIN_ATTEMPT_HMAC_KEY",
        "MINI_MARKETO_LOGIN_ATTEMPT_WINDOW_SECONDS",
        "MINI_MARKETO_LOGIN_ATTEMPT_ACCOUNT_LIMIT",
        "MINI_MARKETO_LOGIN_ATTEMPT_IP_LIMIT",
        "MINI_MARKETO_LOGIN_ATTEMPT_INSTALLATION_LIMIT",
    )
    for name in names:
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(ValueError, match="MINI_MARKETO_ADMIN_ORIGIN"):
        production_create_app()

    with pytest.raises(ValueError, match="independentes"):
        production_create_app(
            web_security_config=WebSecurityConfig(
                TEST_WEB_CONFIG.origin, b"x" * 32, 3600, 1800, 900
            ),
            login_attempt_config=type(TEST_LOGIN_CONFIG)(
                b"x" * 32, 60, 5, 30, 100
            ),
        )

    with pytest.raises(ValueError, match="fabrica explicita"):
        production_create_app(
            service=object(),
            web_security_config=TEST_WEB_CONFIG,
            login_attempt_config=TEST_LOGIN_CONFIG,
        )


def test_access_repository_factory_uses_a_fresh_closed_sqlite_connection(tmp_path):
    factory = build_access_repository_factory(tmp_path / "factory.db")
    with factory() as first:
        first_connection = first.conn
    with pytest.raises(Exception):
        first_connection.execute("SELECT 1")
    with factory() as second:
        assert second.conn is not first_connection
        assert second.conn.execute("SELECT 1").fetchone()[0] == 1


def test_cleanup_removes_only_one_expired_batch(integrated):
    now = int(time.time())
    with integrated.repos() as repo:
        user = repo.get_by_username("admin")
        assert repo.create_session("a" * 64, user.id, user.credential_version, 10, 20)
        assert repo.create_session("b" * 64, user.id, user.credential_version, 10, 20)
        assert repo.create_session("c" * 64, user.id, user.credential_version, now, now + 600)
        if integrated.db.kind == "sqlite":
            p = "?"
        else:
            p = "%s"
        for digest in ("d" * 64, "e" * 64):
            integrated.db.execute(
                "INSERT INTO admin_login_attempts "
                f"(bucket_hash, started_at, expires_at, attempts) VALUES ({p}, {p}, {p}, 1)",
                (digest, 10, 20),
            )
        assert repo.cleanup_expired_sessions(now, batch_size=1) == 1
        assert repo.cleanup_expired_login_attempts(now, batch_size=1) == 1
        assert repo.cleanup_expired_sessions(now, batch_size=1) == 1
        assert repo.cleanup_expired_login_attempts(now, batch_size=1) == 1
        assert repo.cleanup_expired_sessions(now, batch_size=1) == 0
        assert repo.cleanup_expired_login_attempts(now, batch_size=1) == 0
    assert integrated.db.scalar("SELECT count(*) FROM admin_sessions") == 1
    assert integrated.db.scalar("SELECT count(*) FROM admin_login_attempts") == 0
