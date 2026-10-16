from dataclasses import replace
from http.cookies import SimpleCookie

import pytest
from starlette.responses import Response

from mini_marketo.web_security import (
    WebSecurityConfig, clear_access_cookies, private_response,
    set_login_cookie, set_session_cookie,
)


@pytest.fixture
def config():
    return WebSecurityConfig("https://admin.example.test", b"test-only-key-" * 3, 100, 20, 30)


def test_secure_cookie_attributes_and_remaining_lifetime(config):
    response = Response()
    set_session_cookie(response, config, "A" * 43, expires_at=200, now=150)
    cookies = SimpleCookie()
    cookies.load(response.headers["set-cookie"])
    cookie = cookies[config.session_cookie]
    assert cookie.value == "A" * 43
    assert cookie["max-age"] == "50"
    assert cookie["secure"] and cookie["httponly"]
    assert cookie["samesite"] == "lax" and cookie["path"] == "/"
    assert not cookie["domain"]
    assert response.headers["cache-control"] == "no-store"


def test_login_cookie_and_clear_use_matching_scope(config):
    response = Response()
    set_login_cookie(response, config, "B" * 43)
    cookies = SimpleCookie()
    cookies.load(response.headers["set-cookie"])
    assert cookies[config.login_cookie]["max-age"] == "30"
    expired = Response()
    clear_access_cookies(expired, config)
    values = expired.headers.getlist("set-cookie")
    assert len(values) == 2
    for value in values:
        parsed = SimpleCookie(value)
        cookie = next(iter(parsed.values()))
        assert cookie["max-age"] == "0"
        assert cookie["secure"] and cookie["httponly"]
        assert cookie["path"] == "/" and not cookie["domain"]


def test_local_cookie_is_explicitly_distinct(config):
    local = replace(config, origin="http://localhost:8000", allow_http_local=True)
    response = Response()
    set_session_cookie(response, local, "A" * 43, expires_at=200, now=150)
    cookie = SimpleCookie(response.headers["set-cookie"])[local.session_cookie]
    assert not cookie["secure"] and cookie["httponly"]


@pytest.mark.parametrize("token,expires,now", [(None, 200, 150), ("bad", 200, 150), ("A" * 43, 150, 150), ("A" * 43, 300, 150), ("A" * 43, 200, True)])
def test_invalid_cookie_input_does_not_set_header(config, token, expires, now):
    response = Response()
    with pytest.raises(ValueError):
        set_session_cookie(response, config, token, expires_at=expires, now=now)
    assert "set-cookie" not in response.headers


def test_private_response_headers():
    response = Response()
    private_response(response)
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
