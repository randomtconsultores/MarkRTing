import asyncio

import pytest

from mini_marketo.admin_guard import AdminAccessGuard
from mini_marketo.web_security import CsrfTokens, WebSecurityConfig


@pytest.mark.parametrize("response_started", [False, True])
def test_downstream_errors_before_and_after_response_start(
    monkeypatch, caplog, response_started
):
    config = WebSecurityConfig(
        "https://admin.example.test", b"response-errors-test-key-32-bytes!", 3600, 1800, 900
    )
    token = "s" * 43
    csrf = CsrfTokens(config)
    access = {
        "role": "admin",
        "admin_csrf_token": csrf.issue(token, "admin", now=1000),
        "logout_csrf_token": csrf.issue(token, "logout", now=1000),
    }
    marker = "SENSITIVE-DOWNSTREAM-ERROR-MARKER"
    error = RuntimeError(marker)
    messages = []

    async def downstream(scope, receive, send):
        if response_started:
            await send({"type": "http.response.start", "status": 200, "headers": []})
        raise error

    async def receive():
        return {"type": "http.disconnect"}

    async def send(message):
        messages.append(message)

    guard = AdminAccessGuard(
        downstream,
        repo_factory=lambda: None,
        web_config=config,
        max_body_bytes=4096,
        max_form_fields=128,
        clock=lambda: 1000,
    )
    # Isolate downstream error handling; authentication has separate coverage.
    monkeypatch.setattr(guard, "_resolve_access_context", lambda value, now: access)
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/contacts",
        "headers": [(b"cookie", f"{config.session_cookie}={token}".encode("ascii"))],
    }
    if response_started:
        with pytest.raises(RuntimeError) as caught:
            asyncio.run(guard(scope, receive, send))
        assert caught.value is error
        assert [message["type"] for message in messages] == ["http.response.start"]
        assert messages[0]["status"] == 200
    else:
        asyncio.run(guard(scope, receive, send))
        assert [message["type"] for message in messages] == [
            "http.response.start", "http.response.body"
        ]
        assert messages[0]["status"] == 500
        assert marker.encode("ascii") not in messages[1]["body"]
    headers = dict(messages[0]["headers"])
    assert headers[b"cache-control"] == b"no-store"
    assert headers[b"referrer-policy"] == b"no-referrer"
    assert headers[b"x-content-type-options"] == b"nosniff"
    assert headers[b"x-frame-options"] == b"DENY"
    assert marker not in caplog.text
    assert "Falha ao processar um pedido administrativo." in caplog.text
