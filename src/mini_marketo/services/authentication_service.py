"""Credential verification and session issuance; no HTTP endpoint or rate limiter."""

import secrets

from mini_marketo import passwords
from mini_marketo.services.session_service import IssuedSession, SessionService

# Generated once per process, not per request. This is not an account credential.
_DUMMY_HASH = passwords.hash_password(secrets.token_urlsafe(32))


def _valid_username(username):
    if not isinstance(username, str) or not 1 <= len(username) <= 128:
        return False
    try:
        username.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return "\x00" not in username and username == username.strip().lower()


class AuthenticationService:
    """Coordinate trusted application calls; do not expose without web protections.

    Use one repository/request. Password hashing is synchronous and must not run
    directly on an async event loop. Infrastructure failures propagate.
    """

    def __init__(self, repo, *, absolute_seconds: int, idle_seconds: int):
        self.repo = repo
        self.sessions = SessionService(
            repo, absolute_seconds=absolute_seconds, idle_seconds=idle_seconds,
        )

    def authenticate(self, username: str, password: str, *, now: int) -> IssuedSession | None:
        """Return a fresh session or the same failure result for invalid credentials.

        Do not refresh the credential version after checking the password. The
        repository must reject issuance if that observed version is now stale.
        No automatic rehash: its persistence/revocation policy is not defined yet.
        """
        if type(now) is not int or not 0 <= now <= 2**63 - 1:
            raise ValueError("now tem de ser um segundo Unix valido.")
        if now + self.sessions.absolute_seconds > 2**63 - 1:
            raise ValueError("A expiracao excede o intervalo suportado.")
        user = self.repo.get_by_username(username) if _valid_username(username) else None
        stored_hash = user.password_hash if user is not None else _DUMMY_HASH
        verified = passwords.verify_password(stored_hash, password)
        if user is None or not verified or not user.is_active:
            return None
        if user.role not in ("admin", "viewer"):
            return None
        return self.sessions.issue(user.id, user.credential_version, now=now)
