"""Test-only authenticated client helpers for legacy administrative HTTP tests."""

from collections.abc import Mapping
import tempfile
import time
from urllib.parse import urlencode, urlsplit
import uuid

from fastapi.testclient import TestClient as StarletteTestClient

from mini_marketo.access_policy import classify_route, csrf_purpose
from mini_marketo.main import create_app as production_create_app
from mini_marketo.services.login_attempt_limiter import LoginAttemptConfig
from mini_marketo.services.session_service import SessionService
from mini_marketo.web_security import CsrfTokens, WebSecurityConfig


TEST_WEB_CONFIG = WebSecurityConfig(
    "https://admin.example.test",
    b"test-only-csrf-key-not-for-production-0001",
    3600,
    1800,
    900,
)
TEST_LOGIN_CONFIG = LoginAttemptConfig(
    b"test-only-rate-key-not-for-production-0001",
    60,
    5,
    30,
    100,
)


def create_app(*args, **kwargs):
    kwargs.setdefault("web_security_config", TEST_WEB_CONFIG)
    kwargs.setdefault("login_attempt_config", TEST_LOGIN_CONFIG)
    has_injected_service = any(
        kwargs.get(name) is not None
        for name in (
            "service",
            "segment_service",
            "consent_service",
            "campaign_service",
            "unsubscribe_service",
            "suppression_service",
            "subscription_service",
            "confirmation_service",
        )
    )
    if has_injected_service and kwargs.get("access_repo_factory") is None:
        temp_dir = tempfile.TemporaryDirectory(prefix="mini-marketo-access-test-")
        from mini_marketo.main import build_access_repository_factory
        kwargs["access_repo_factory"] = build_access_repository_factory(
            f"{temp_dir.name}/admin.sqlite"
        )
        app = production_create_app(*args, **kwargs)
        app.state.admin_test_temp_dir = temp_dir
    else:
        app = production_create_app(*args, **kwargs)

    factory = app.state.access_repo_factory
    with factory() as repo:
        username = f"legacy-test-{uuid.uuid4().hex}"
        user = repo.create_user(username, "not-a-real-password-hash", "admin", int(time.time()))
    app.state.admin_test_user_id = user.id
    app.state.admin_test_access_factory = factory
    return app


class TestClient(StarletteTestClient):
    def __init__(self, app, **kwargs):
        kwargs.setdefault("base_url", TEST_WEB_CONFIG.origin)
        super().__init__(app, **kwargs)
        user_id = getattr(app.state, "admin_test_user_id", None)
        if user_id is not None:
            with app.state.admin_test_access_factory() as repo:
                user = repo.get_user(user_id)
                issued = SessionService(
                    repo,
                    absolute_seconds=TEST_WEB_CONFIG.absolute_seconds,
                    idle_seconds=TEST_WEB_CONFIG.idle_seconds,
                ).issue(user.id, user.credential_version, now=int(time.time()))
            if issued is None:
                raise RuntimeError("Nao foi possivel preparar a sessao de teste.")
            self._admin_test_session_token = issued.token
            self.cookies.set(
                TEST_WEB_CONFIG.session_cookie,
                self._admin_test_session_token,
                domain="admin.example.test",
                path="/",
            )

    def request(self, method, url, **kwargs):
        parts = urlsplit(str(url))
        path = parts.path
        kind = classify_route(method, path)
        purpose = csrf_purpose(method, kind)
        if purpose is not None:
            token = getattr(self, "_admin_test_session_token", None)
            if token:
                data = kwargs.get("data")
                if isinstance(data, Mapping):
                    fields = dict(data)
                elif isinstance(data, (list, tuple)):
                    fields = list(data)
                else:
                    fields = []
                csrf_token = CsrfTokens(TEST_WEB_CONFIG).issue(
                    token, purpose, now=int(time.time())
                )
                if isinstance(fields, dict):
                    if "csrf_token" not in fields:
                        fields["csrf_token"] = csrf_token
                    kwargs["data"] = fields
                elif not any(name == "csrf_token" for name, _ in fields):
                    fields.append(("csrf_token", csrf_token))
                    kwargs.pop("data", None)
                    kwargs["content"] = urlencode(fields).encode("ascii")
                    headers = dict(kwargs.get("headers") or {})
                    headers.setdefault(
                        "content-type", "application/x-www-form-urlencoded"
                    )
                    kwargs["headers"] = headers
                headers = dict(kwargs.get("headers") or {})
                headers.setdefault("origin", TEST_WEB_CONFIG.origin)
                kwargs["headers"] = headers
        return super().request(method, url, **kwargs)


__all__ = ["TestClient", "create_app"]
