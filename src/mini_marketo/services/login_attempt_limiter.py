"""Persistent, HMAC-keyed budgets for administrative login attempts."""

import hashlib
import hmac
import ipaddress
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass

_MAX_TIME = 2**63 - 1
_KEY_MIN_BYTES = 32
_CONTEXT = b"mini-marketo:admin-login-attempt:v1\x00"
_POSITIVE_INTEGER = re.compile(r"[0-9]+")


def _positive(value, name):
    if type(value) is not int or not 0 < value <= _MAX_TIME:
        raise ValueError(f"{name} tem de ser um inteiro positivo valido.")
    return value


@dataclass(frozen=True)
class LoginAttemptConfig:
    hmac_key: bytes
    window_seconds: int
    account_limit: int
    ip_limit: int
    installation_limit: int

    def __post_init__(self):
        if not isinstance(self.hmac_key, bytes) or len(self.hmac_key) < _KEY_MIN_BYTES:
            raise ValueError("A chave HMAC tem de conter pelo menos 32 bytes.")
        _positive(self.window_seconds, "window_seconds")
        _positive(self.account_limit, "account_limit")
        _positive(self.ip_limit, "ip_limit")
        _positive(self.installation_limit, "installation_limit")

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "LoginAttemptConfig":
        env = os.environ if env is None else env
        names = {
            "hmac_key": "MINI_MARKETO_LOGIN_ATTEMPT_HMAC_KEY",
            "window_seconds": "MINI_MARKETO_LOGIN_ATTEMPT_WINDOW_SECONDS",
            "account_limit": "MINI_MARKETO_LOGIN_ATTEMPT_ACCOUNT_LIMIT",
            "ip_limit": "MINI_MARKETO_LOGIN_ATTEMPT_IP_LIMIT",
            "installation_limit": "MINI_MARKETO_LOGIN_ATTEMPT_INSTALLATION_LIMIT",
        }
        values = {}
        for field, name in names.items():
            raw = env.get(name, "")
            if not raw:
                raise ValueError(f"{name} e obrigatoria; nao ha limites de producao predefinidos.")
            if field == "hmac_key":
                values[field] = raw.encode("utf-8")
                continue
            if _POSITIVE_INTEGER.fullmatch(raw) is None:
                raise ValueError(f"{name} tem de ser um inteiro positivo.")
            try:
                values[field] = int(raw)
            except ValueError as exc:
                raise ValueError(f"{name} tem de ser um inteiro positivo.") from exc
        return cls(**values)


class LoginAttemptLimiter:
    """Reserve account, IP, and installation budgets in one repository transaction.

    The username argument is used only to derive a normalized budget key. Callers
    must pass the original, unmodified username to credential authentication.
    """

    def __init__(self, repo, *, config: LoginAttemptConfig):
        self.repo = repo
        self.config = config

    def reserve(self, username: str, remote_ip: str, *, now: int):
        if not isinstance(username, str):
            raise ValueError("username tem de ser texto.")
        if not isinstance(remote_ip, str):
            raise ValueError("remote_ip tem de ser texto.")
        if type(now) is not int or not 0 <= now <= _MAX_TIME:
            raise ValueError("now tem de ser um segundo Unix valido.")
        if now + self.config.window_seconds > _MAX_TIME:
            raise ValueError("A expiracao excede o intervalo suportado.")
        account_key = username.strip().lower().encode("utf-8")
        try:
            address = ipaddress.ip_address(remote_ip)
        except ValueError as exc:
            raise ValueError("remote_ip tem de ser um endereco IP valido.") from exc
        if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
            address = address.ipv4_mapped
        ip_key = address.compressed.encode("ascii")
        buckets = (
            (self._bucket_hash("account", account_key), self.config.account_limit),
            (self._bucket_hash("ip", ip_key), self.config.ip_limit),
            (self._bucket_hash("installation", b""), self.config.installation_limit),
        )
        return self.repo.reserve_login_attempts(
            buckets, now, self.config.window_seconds
        )

    def cleanup_expired(self, *, now: int) -> int:
        return self.repo.cleanup_expired_login_attempts(now)

    def _bucket_hash(self, scope: str, subject: bytes) -> str:
        scope_bytes = scope.encode("ascii")
        message = (
            _CONTEXT
            + len(scope_bytes).to_bytes(2, "big")
            + scope_bytes
            + len(subject).to_bytes(4, "big")
            + subject
        )
        return hmac.new(self.config.hmac_key, message, hashlib.sha256).hexdigest()
