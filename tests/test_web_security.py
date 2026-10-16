from dataclasses import replace

import pytest

from mini_marketo.web_security import (
    CsrfTokens, WebSecurityConfig, safe_redirect, same_origin,
)


@pytest.fixture
def config():
    return WebSecurityConfig("https://admin.example.test", b"test-only-key-" * 3, 100, 20, 30)


@pytest.mark.parametrize("origin", ["http://admin.example.test", "https://user:pass@admin.example.test", "https://admin.example.test/path", "https://admin.example.test/?q=1", "https://admin.example.test/#x", "https://admin.example.test:99999", "https://admin.example.test\r\n", "https://admin.example.test\\@evil.test", "https://a..test", "", None])
def test_invalid_config_origin(config, origin):
    with pytest.raises(ValueError):
        replace(config, origin=origin)


@pytest.mark.parametrize("changes", [
    {"csrf_key": b"short"}, {"csrf_key": "x" * 32},
    {"absolute_seconds": 0}, {"idle_seconds": True}, {"idle_seconds": 101},
    {"csrf_seconds": 101}, {"csrf_seconds": -1}, {"allow_http_local": 1},
    {"allow_http_local": True}, {"absolute_seconds": 2**63},
])
def test_invalid_config_values(config, changes):
    with pytest.raises(ValueError):
        replace(config, **changes)


def test_default_port_and_case_normalized(config):
    assert replace(config, origin="https://ADMIN.example.test:443/").origin == config.origin
    assert config.csrf_key.decode() not in repr(config)


@pytest.mark.parametrize("host", ["localhost", "127.0.0.1", "[::1]"])
def test_explicit_local_http(config, host):
    local = replace(config, origin=f"http://{host}:8000", allow_http_local=True)
    assert not local.secure
    assert not local.session_cookie.startswith("__Host-")
    assert local.session_cookie != config.session_cookie


@pytest.mark.parametrize("host", ["localhost.evil.test", "0.0.0.0", "192.168.1.1", "127.1", "local.test"])
def test_http_exception_cannot_enable_other_hosts(config, host):
    with pytest.raises(ValueError):
        replace(config, origin=f"http://{host}", allow_http_local=True)


@pytest.mark.parametrize("value", ["/", "/contacts", "/segments/12/edit", "/contacts?page=2&per_page=20"])
def test_safe_local_redirect(value):
    assert safe_redirect(value) == value


@pytest.mark.parametrize("value", [None, 42, "", "https://evil.test", "//evil.test", "///evil.test", "javascript:alert(1)", "contacts", "/%2fevil.test", "/%252fevil.test", "/\\evil.test", "/x\r\nLocation:https://evil.test", " /contacts", "/contacts#fragment", "/café", "/" + "a" * 2048])
def test_unsafe_redirect_falls_back(value):
    assert safe_redirect(value) == "/"


@pytest.mark.parametrize("origin,referer,expected", [
    ("https://admin.example.test", None, True),
    ("https://ADMIN.example.test:443", None, True),
    (None, "https://admin.example.test/contacts?id=1", True),
    ("https://evil.test", "https://admin.example.test/", False),
    ("null", "https://admin.example.test/", False),
    ("", "https://admin.example.test/", False),
    (None, None, False),
    ("https://admin.example.test.evil.test", None, False),
    ("http://admin.example.test", None, False),
    ("https://admin.example.test:444", None, False),
    ("https://admin.example.test/path", None, False),
    ("https://user@admin.example.test", None, False),
])
def test_origin_validation(config, origin, referer, expected):
    assert same_origin(config, origin=origin, referer=referer) is expected


@pytest.mark.parametrize("purpose", ["login", "admin", "logout"])
def test_csrf_roundtrip_and_expiry(config, purpose):
    tokens = CsrfTokens(config)
    token = tokens.issue("A" * 43, purpose, now=100)
    assert tokens.verify(token, "A" * 43, purpose, now=100)
    assert tokens.verify(token, "A" * 43, purpose, now=129)
    assert not tokens.verify(token, "A" * 43, purpose, now=130)
    assert not tokens.verify(token, "A" * 43, purpose, now=99)
    assert "A" * 43 not in token


def test_csrf_bound_to_key_session_and_purpose(config):
    tokens = CsrfTokens(config)
    token = tokens.issue("A" * 43, "admin", now=100)
    assert not tokens.verify(token, "B" * 43, "admin", now=100)
    assert not tokens.verify(token, "A" * 43, "login", now=100)
    assert not CsrfTokens(replace(config, csrf_key=b"other-test-key-" * 3)).verify(token, "A" * 43, "admin", now=100)
    assert tokens.issue("A" * 43, "admin", now=100) != token


@pytest.mark.parametrize("token", [None, 42, b"bytes", "", "x" * 129, "é" * 100, "100.x.y", "0100." + "A" * 43 + "." + "a" * 64])
def test_malformed_csrf(config, token):
    assert not CsrfTokens(config).verify(token, "A" * 43, "admin", now=100)


@pytest.mark.parametrize("component", [0, 1, 2])
def test_csrf_tampering_rejected(config, component):
    tokens = CsrfTokens(config)
    parts = tokens.issue("A" * 43, "admin", now=100).split(".")
    parts[component] = ["101", "B" * 43, "0" * 64][component]
    assert not tokens.verify(".".join(parts), "A" * 43, "admin", now=102)


@pytest.mark.parametrize("binding,purpose", [(None, "admin"), ("short", "admin"), ("é" * 43, "admin"), ("A" * 43, "unknown")])
def test_invalid_csrf_context(config, binding, purpose):
    tokens = CsrfTokens(config)
    with pytest.raises(ValueError):
        tokens.issue(binding, purpose, now=100)
    assert not tokens.verify("x", binding, purpose, now=100)


@pytest.mark.parametrize("now", [-1, True, 1.0, None, 2**63])
def test_invalid_clock(config, now):
    with pytest.raises(ValueError):
        CsrfTokens(config).issue("A" * 43, "login", now=now)
    with pytest.raises(ValueError):
        CsrfTokens(config).verify("x", "A" * 43, "login", now=now)


def test_csrf_expiry_overflow(config):
    with pytest.raises(ValueError):
        CsrfTokens(config).issue("A" * 43, "login", now=2**63 - 1)

