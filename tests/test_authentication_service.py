from dataclasses import replace
import hashlib

import pytest

from mini_marketo import passwords
from mini_marketo.repositories.access_repository import AdminUser
from mini_marketo.services import authentication_service
from mini_marketo.services.authentication_service import AuthenticationService


@pytest.fixture(scope="module")
def stored_hash():
    return passwords.hash_password(" frase correta 🔐 ")


class RecordingRepository:
    """Unit-test double, not a substitute for database integration tests."""

    def __init__(self, user):
        self.user = user
        self.lookups = []
        self.issued = []

    def get_by_username(self, username):
        self.lookups.append(username)
        return self.user if self.user and username == self.user.username else None

    def create_session(self, token_hash, user_id, version, now, expires_at):
        if not self.user or not self.user.is_active or version != self.user.credential_version:
            return False
        self.issued.append((token_hash, user_id, version, now, expires_at))
        return True


@pytest.fixture
def repo(stored_hash):
    return RecordingRepository(AdminUser(1, "operator", stored_hash, "admin", True, 1, 100))


def service(repo):
    return AuthenticationService(repo, absolute_seconds=100, idle_seconds=10)


@pytest.mark.parametrize("role", ["admin", "viewer"])
def test_success_uses_real_argon2_and_session_service(repo, role):
    repo.user = replace(repo.user, role=role)
    issued = service(repo).authenticate("operator", " frase correta 🔐 ", now=100)
    assert issued is not None
    assert issued.expires_at == 200
    assert issued.token not in repr(issued)
    assert repo.lookups == ["operator"]
    assert repo.issued == [(hashlib.sha256(issued.token.encode()).hexdigest(), 1, 1, 100, 200)]


@pytest.mark.parametrize("username,password,inactive", [
    ("operator", "wrong", False), ("missing", " frase correta 🔐 ", False),
    ("operator", " frase correta 🔐 ", True),
    ("operator", "frase correta 🔐", False),
])
def test_generic_failure_never_issues_session(repo, username, password, inactive):
    if inactive:
        repo.user = replace(repo.user, is_active=False)
    assert service(repo).authenticate(username, password, now=100) is None
    assert repo.issued == []


@pytest.mark.parametrize("username", [None, 12, b"bytes", "", "Operator", " operator", "x" * 129, "bad\x00name", "\ud800"])
def test_bad_username_uses_dummy_verification_without_lookup(repo, username, monkeypatch):
    calls = []
    def verify(stored, supplied):
        calls.append((stored, supplied))
        return True
    monkeypatch.setattr(passwords, "verify_password", verify)
    assert service(repo).authenticate(username, "supplied", now=100) is None
    assert calls == [(authentication_service._DUMMY_HASH, "supplied")]
    assert repo.lookups == []
    assert repo.issued == []


def test_absent_user_still_verifies_dummy_hash(repo, monkeypatch):
    calls = []
    def verify(stored, supplied):
        calls.append((stored, supplied))
        return True
    monkeypatch.setattr(passwords, "verify_password", verify)
    assert service(repo).authenticate("absent", "supplied", now=100) is None
    assert calls == [(authentication_service._DUMMY_HASH, "supplied")]
    assert repo.issued == []


def test_inactive_user_still_verifies_password(repo, monkeypatch):
    repo.user = replace(repo.user, is_active=False)
    calls = []
    def verify(stored, supplied):
        calls.append((stored, supplied))
        return True
    monkeypatch.setattr(passwords, "verify_password", verify)
    assert service(repo).authenticate("operator", "supplied", now=100) is None
    assert calls == [(repo.user.password_hash, "supplied")]


@pytest.mark.parametrize("password", [None, 42, b"bytes", "", "x" * 4097, "\ud800"])
def test_bad_password_rejected(repo, password):
    assert service(repo).authenticate("operator", password, now=100) is None
    assert repo.issued == []


def test_unknown_role_rejected(repo):
    repo.user = replace(repo.user, role="unknown")
    assert service(repo).authenticate("operator", " frase correta 🔐 ", now=100) is None
    assert repo.issued == []


def test_corrupt_hash_rejected(repo):
    repo.user = replace(repo.user, password_hash="corrupt")
    assert service(repo).authenticate("operator", " frase correta 🔐 ", now=100) is None
    assert repo.issued == []


@pytest.mark.parametrize("change", ["password", "inactive", "role"])
def test_change_during_verification_keeps_original_version(repo, monkeypatch, change):
    real_verify = passwords.verify_password
    def verify(stored, supplied):
        valid = real_verify(stored, supplied)
        repo.user = replace(repo.user, credential_version=2,
                            is_active=change != "inactive",
                            role="viewer" if change == "role" else "admin")
        return valid
    monkeypatch.setattr(passwords, "verify_password", verify)
    assert service(repo).authenticate("operator", " frase correta 🔐 ", now=100) is None
    assert repo.lookups == ["operator"]
    assert repo.issued == []


@pytest.mark.parametrize("now", [-1, True, 1.0, None, 2**63, 2**63 - 1])
def test_invalid_time_fails_before_lookup(repo, now):
    with pytest.raises(ValueError):
        service(repo).authenticate("operator", "supplied", now=now)
    assert repo.lookups == []
    assert repo.issued == []


def test_storage_failure_propagates(repo, monkeypatch):
    def fail(*args):
        raise RuntimeError("database unavailable")
    monkeypatch.setattr(repo, "get_by_username", fail)
    with pytest.raises(RuntimeError, match="database unavailable"):
        service(repo).authenticate("operator", "supplied", now=100)


def test_issuance_failure_propagates(repo, monkeypatch):
    def fail(*args):
        raise RuntimeError("database unavailable")
    monkeypatch.setattr(repo, "create_session", fail)
    with pytest.raises(RuntimeError, match="database unavailable"):
        service(repo).authenticate("operator", " frase correta 🔐 ", now=100)


def test_new_session_on_each_success(repo):
    auth = service(repo)
    first = auth.authenticate("operator", " frase correta 🔐 ", now=100)
    second = auth.authenticate("operator", " frase correta 🔐 ", now=100)
    assert first.token != second.token
    assert len(repo.issued) == 2
