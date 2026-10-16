"""Web security primitives; importing this module does not protect any route."""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import hmac
import ipaddress
import json
import re
import secrets
from urllib.parse import urlsplit

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from starlette.responses import Response

_TOKEN = re.compile(r"[A-Za-z0-9_-]{43}")
_MAX_TIME = 2**63 - 1


def _time(value):
    if type(value) is not int or not 0 <= value <= _MAX_TIME:
        raise ValueError("Data invalida.")
    return value


def _duration(value):
    if type(value) is not int or not 0 < value <= _MAX_TIME:
        raise ValueError("Duracao invalida.")
    return value


def _url(value):
    if not isinstance(value, str) or not value or len(value) > 2048:
        raise ValueError("URL invalido.")
    if not value.isascii() or any(ord(c) <= 32 or ord(c) == 127 for c in value) or "\\" in value:
        raise ValueError("URL invalido.")
    try:
        parts = urlsplit(value)
        if parts.scheme not in ("https", "http") or not parts.hostname or parts.username is not None or parts.password is not None:
            raise ValueError("Origem invalida.")
        host = parts.hostname.lower()
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            if not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?", host) or ".." in host:
                raise ValueError("Host invalido.")
            rendered = host
        else:
            rendered = f"[{host}]" if address.version == 6 else host
        port = parts.port
        if port is not None and not 1 <= port <= 65535:
            raise ValueError("Porta invalida.")
    except ValueError:
        raise ValueError("URL invalido.") from None
    suffix = "" if port is None or port == (443 if parts.scheme == "https" else 80) else f":{port}"
    return parts, f"{parts.scheme}://{rendered}{suffix}"


@dataclass(frozen=True)
class WebSecurityConfig:
    origin: str
    csrf_key: bytes = field(repr=False)
    absolute_seconds: int
    idle_seconds: int
    csrf_seconds: int
    allow_http_local: bool = False

    def __post_init__(self):
        parts, origin = _url(self.origin)
        if parts.path not in ("", "/") or parts.query or parts.fragment:
            raise ValueError("Configura apenas a origem, sem caminho, query ou fragmento.")
        if type(self.allow_http_local) is not bool:
            raise ValueError("Excecao local invalida.")
        if parts.scheme == "http":
            if not self.allow_http_local or parts.hostname not in ("localhost", "127.0.0.1", "::1"):
                raise ValueError("HTTP apenas em desenvolvimento local explicito.")
        elif self.allow_http_local:
            raise ValueError("A excecao HTTP nao se aplica a HTTPS.")
        if not isinstance(self.csrf_key, bytes) or len(self.csrf_key) < 32:
            raise ValueError("Chave CSRF deve ter pelo menos 32 bytes.")
        for value in (self.absolute_seconds, self.idle_seconds, self.csrf_seconds):
            _duration(value)
        if self.idle_seconds > self.absolute_seconds or self.csrf_seconds > self.absolute_seconds:
            raise ValueError("Duracoes incompativeis.")
        object.__setattr__(self, "origin", origin)

    @property
    def secure(self):
        return self.origin.startswith("https://")

    @property
    def session_cookie(self):
        return "__Host-mm_session" if self.secure else "mm_session_local"

    @property
    def login_cookie(self):
        return "__Host-mm_login" if self.secure else "mm_login_local"


def safe_redirect(target):
    """Strict local redirects; encoded, ambiguous or non-ASCII inputs fall back to /."""
    if not isinstance(target, str) or not 1 <= len(target) <= 2048 or not target.isascii():
        return "/"
    if not target.startswith("/") or target.startswith("//"):
        return "/"
    if any(ord(c) <= 32 or ord(c) == 127 for c in target) or any(c in target for c in ("%", "\\", "#")):
        return "/"
    try:
        parts = urlsplit(target)
    except ValueError:
        return "/"
    return target if not parts.scheme and not parts.netloc else "/"


def same_origin(config, *, origin=None, referer=None):
    """Origin takes precedence; absence or null never permits a mutation."""
    supplied = origin if origin is not None else referer
    try:
        parts, normalized = _url(supplied)
    except ValueError:
        return False
    if origin is not None and (parts.path not in ("", "/") or parts.query or parts.fragment):
        return False
    return normalized == config.origin


class CsrfTokens:
    """HMAC tokens bound to a session or login nonce and purpose, never authentication."""

    def __init__(self, config):
        self.config = config

    def _mac(self, issued, nonce, binding, purpose):
        message = json.dumps(["mm-csrf-v1", issued, nonce, binding, purpose], separators=(",", ":")).encode("ascii")
        return hmac.new(self.config.csrf_key, message, hashlib.sha256).hexdigest()

    def issue(self, binding, purpose, *, now):
        _time(now)
        if not isinstance(binding, str) or _TOKEN.fullmatch(binding) is None or purpose not in ("login", "admin", "logout"):
            raise ValueError("Vinculo CSRF invalido.")
        if now + self.config.csrf_seconds > _MAX_TIME:
            raise ValueError("Expiracao invalida.")
        nonce = secrets.token_urlsafe(32)
        return f"{now}.{nonce}.{self._mac(now, nonce, binding, purpose)}"

    def verify(self, token, binding, purpose, *, now):
        _time(now)
        if not isinstance(token, str) or len(token) > 128 or not token.isascii():
            return False
        if not isinstance(binding, str) or _TOKEN.fullmatch(binding) is None or purpose not in ("login", "admin", "logout"):
            return False
        parts = token.split(".")
        if len(parts) != 3:
            return False
        timestamp, nonce, signature = parts
        if not re.fullmatch(r"0|[1-9][0-9]{0,18}", timestamp) or _TOKEN.fullmatch(nonce) is None:
            return False
        if re.fullmatch(r"[0-9a-f]{64}", signature) is None:
            return False
        issued = int(timestamp)
        if not 0 <= issued <= now or now - issued >= self.config.csrf_seconds:
            return False
        return hmac.compare_digest(signature, self._mac(issued, nonce, binding, purpose))


def _cookie(response, config, name, value, seconds):
    if not isinstance(value, str) or _TOKEN.fullmatch(value) is None:
        raise ValueError("Token de cookie invalido.")
    response.set_cookie(name, value, max_age=_duration(seconds), path="/",
                        secure=config.secure, httponly=True, samesite="lax")
    private_response(response)


def set_session_cookie(response: Response, config, token, *, expires_at, now):
    _time(now)
    _time(expires_at)
    remaining = expires_at - now
    if not 0 < remaining <= config.absolute_seconds:
        raise ValueError("Expiracao de sessao invalida.")
    _cookie(response, config, config.session_cookie, token, remaining)


def set_login_cookie(response: Response, config, nonce):
    _cookie(response, config, config.login_cookie, nonce, config.csrf_seconds)


def clear_access_cookies(response: Response, config):
    """Expire browser cookies only; caller must also revoke the server-side session."""
    for name in (config.session_cookie, config.login_cookie):
        response.delete_cookie(name, path="/", secure=config.secure, httponly=True, samesite="lax")
    private_response(response)


def private_response(response: Response):
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
