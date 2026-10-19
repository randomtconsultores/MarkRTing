"""Focused tests for explicit administrative web configuration; no database needed."""

import pytest

from mini_marketo.web_security import CsrfTokens, WebSecurityConfig


@pytest.fixture
def env():
    return {
        "MINI_MARKETO_ADMIN_ORIGIN": "https://admin.example.test",
        "MINI_MARKETO_ADMIN_CSRF_KEY": "test-only-csrf-key-" * 2,
        "MINI_MARKETO_ADMIN_SESSION_ABSOLUTE_SECONDS": "3600",
        "MINI_MARKETO_ADMIN_SESSION_IDLE_SECONDS": "600",
        "MINI_MARKETO_ADMIN_CSRF_SECONDS": "300",
    }


def test_loads_explicit_policy_and_secure_cookie_names(env):
    config = WebSecurityConfig.from_env(env)
    assert config == WebSecurityConfig("https://admin.example.test",
        env["MINI_MARKETO_ADMIN_CSRF_KEY"].encode(), 3600, 600, 300)
    assert config.secure and not config.allow_http_local
    assert config.session_cookie == "__Host-mm_session"
    assert config.login_cookie == "__Host-mm_login"
    assert env["MINI_MARKETO_ADMIN_CSRF_KEY"] not in repr(config)


@pytest.mark.parametrize("variable", [
    "MINI_MARKETO_ADMIN_ORIGIN", "MINI_MARKETO_ADMIN_CSRF_KEY",
    "MINI_MARKETO_ADMIN_SESSION_ABSOLUTE_SECONDS",
    "MINI_MARKETO_ADMIN_SESSION_IDLE_SECONDS", "MINI_MARKETO_ADMIN_CSRF_SECONDS",
])
def test_every_policy_value_is_mandatory(env, variable):
    env.pop(variable)
    with pytest.raises(ValueError, match=variable):
        WebSecurityConfig.from_env(env)


@pytest.mark.parametrize("raw", ["", "0", "-1", "1.5", " 60", "+60", "1e2", "9" * 20, str(2**63)])
def test_invalid_duration_is_rejected(env, raw):
    env["MINI_MARKETO_ADMIN_CSRF_SECONDS"] = raw
    with pytest.raises(ValueError):
        WebSecurityConfig.from_env(env)


@pytest.mark.parametrize("variable", ["MINI_MARKETO_ADMIN_SESSION_IDLE_SECONDS", "MINI_MARKETO_ADMIN_CSRF_SECONDS"])
def test_durations_cannot_exceed_absolute_lifetime(env, variable):
    env[variable] = "3601"
    with pytest.raises(ValueError, match="Duracoes"):
        WebSecurityConfig.from_env(env)


@pytest.mark.parametrize("flag", ["true", "false", "yes", "", True])
def test_local_http_flag_requires_exact_explicit_value(env, flag):
    env["MINI_MARKETO_ADMIN_ALLOW_HTTP_LOCAL"] = flag
    with pytest.raises(ValueError, match="ALLOW_HTTP_LOCAL"):
        WebSecurityConfig.from_env(env)


def test_http_is_refused_without_explicit_local_exception(env):
    env["MINI_MARKETO_ADMIN_ORIGIN"] = "http://localhost:8000"
    with pytest.raises(ValueError, match="HTTP"):
        WebSecurityConfig.from_env(env)
    env["MINI_MARKETO_ADMIN_ALLOW_HTTP_LOCAL"] = "1"
    config = WebSecurityConfig.from_env(env)
    assert not config.secure
    assert config.session_cookie == "mm_session_local"
    assert config.login_cookie == "mm_login_local"


def test_local_exception_does_not_allow_network_http(env):
    env["MINI_MARKETO_ADMIN_ORIGIN"] = "http://admin.example.test"
    env["MINI_MARKETO_ADMIN_ALLOW_HTTP_LOCAL"] = "1"
    with pytest.raises(ValueError, match="HTTP"):
        WebSecurityConfig.from_env(env)


def test_local_exception_is_not_accepted_with_https(env):
    env["MINI_MARKETO_ADMIN_ALLOW_HTTP_LOCAL"] = "1"
    with pytest.raises(ValueError, match="HTTPS"):
        WebSecurityConfig.from_env(env)


@pytest.mark.parametrize("key", ["short", "\ud800" * 32])
def test_invalid_key_is_rejected_without_echoing_it(env, key):
    env["MINI_MARKETO_ADMIN_CSRF_KEY"] = key
    with pytest.raises(ValueError) as error:
        WebSecurityConfig.from_env(env)
    assert key not in str(error.value)


def test_unicode_key_is_encoded_without_silent_trimming(env):
    env["MINI_MARKETO_ADMIN_CSRF_KEY"] = "é" * 16
    assert WebSecurityConfig.from_env(env).csrf_key == ("é" * 16).encode("utf-8")


def test_reads_environment_only_when_no_mapping_is_supplied(env, monkeypatch):
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("MINI_MARKETO_ADMIN_ALLOW_HTTP_LOCAL", "0")
    assert WebSecurityConfig.from_env().origin == env["MINI_MARKETO_ADMIN_ORIGIN"]
    with pytest.raises(ValueError, match="ADMIN_ORIGIN"):
        WebSecurityConfig.from_env({})


def test_loaded_config_uses_existing_csrf_and_expiry_contract(env):
    tokens = CsrfTokens(WebSecurityConfig.from_env(env))
    binding = "a" * 43
    token = tokens.issue(binding, "login", now=100)
    assert tokens.verify(token, binding, "login", now=399)
    assert not tokens.verify(token, binding, "admin", now=100)
    assert not tokens.verify(token, binding, "login", now=400)
