"""Opaque administrative sessions; not authentication or authorization."""

from dataclasses import dataclass, field
import hashlib
import re
import secrets

from mini_marketo.repositories.access_repository import AdminSession

_MAX_TIME = 2**63 - 1
_TOKEN = re.compile(r"[A-Za-z0-9_-]{43}")


def _positive(value, name):
    if type(value) is not int or not 0 < value <= _MAX_TIME:
        raise ValueError(f"{name} tem de ser um inteiro positivo valido.")
    return value


def _time(now):
    if type(now) is not int or not 0 <= now <= _MAX_TIME:
        raise ValueError("now tem de ser um segundo Unix valido.")
    return now


def _digest(token):
    if not isinstance(token, str) or _TOKEN.fullmatch(token) is None:
        return None
    return hashlib.sha256(token.encode("ascii")).hexdigest()


@dataclass(frozen=True)
class IssuedSession:
    token: str = field(repr=False)
    expires_at: int


class SessionService:
    """One instance per repository/request; no connections or secrets are global.

    Lifetimes are mandatory application policy, in seconds, without silent defaults.
    This service must not be exposed as a public session-creation endpoint.
    """

    def __init__(self, repo, *, absolute_seconds: int, idle_seconds: int):
        self.repo = repo
        self.absolute_seconds = _positive(absolute_seconds, "absolute_seconds")
        self.idle_seconds = _positive(idle_seconds, "idle_seconds")
        if self.idle_seconds > self.absolute_seconds:
            raise ValueError("O limite de inatividade nao pode exceder a duracao absoluta.")

    def issue(self, user_id: int, credential_version: int, *, now: int) -> IssuedSession | None:
        """Caller must first verify credentials and supply the version observed then.

        The repository rechecks active state/version at insertion. Do not replace
        that version with a fresh lookup after password verification.
        """
        _positive(user_id, "user_id")
        _positive(credential_version, "credential_version")
        now = _time(now)
        expires_at = now + self.absolute_seconds
        if expires_at > _MAX_TIME:
            raise ValueError("A expiracao excede o intervalo suportado.")
        token = secrets.token_urlsafe(32)
        token_hash = _digest(token)
        if token_hash is None:
            raise RuntimeError("O gerador devolveu um token de formato invalido.")
        if not self.repo.create_session(token_hash, user_id, credential_version, now, expires_at):
            return None
        return IssuedSession(token, expires_at)

    def resolve(self, token: str, *, now: int) -> AdminSession | None:
        """Validate and refresh activity atomically; never change absolute expiry.

        A clock earlier than stored activity is rejected, not used to rewind it.
        The result is session metadata, not permission to perform a business action.
        """
        now = _time(now)
        token_hash = _digest(token)
        if token_hash is None:
            return None
        return self.repo.touch_valid_session(token_hash, now, self.idle_seconds)

    def revoke(self, token: str) -> bool:
        token_hash = _digest(token)
        return False if token_hash is None else self.repo.revoke_session(token_hash)
