"""Trusted local account administration, not authorization for HTTP callers."""

import hashlib

from mini_marketo import passwords


class AccountConflict(RuntimeError):
    pass


def validate_username(username):
    if not isinstance(username, str) or not 1 <= len(username) <= 128:
        raise ValueError("Nome de conta invalido.")
    try:
        username.encode("utf-8")
    except UnicodeEncodeError:
        raise ValueError("Nome de conta invalido.") from None
    if "\x00" in username or username != username.strip().lower():
        raise ValueError("O nome deve estar em minusculas, sem NUL ou espacos exteriores.")
    return username


class PasswordPolicy:
    """Require an explicitly supplied, nonempty local SHA-256 blocklist.

    SHA-256 is only a lookup key for the blocklist, never password storage.
    The operator is responsible for the source, coverage and freshness of this list.
    """

    def __init__(self, blocked_digests):
        if isinstance(blocked_digests, (str, bytes)):
            raise ValueError("Lista de bloqueio invalida.")
        try:
            self._blocked = frozenset(blocked_digests)
        except TypeError:
            raise ValueError("Lista de bloqueio invalida.") from None
        if not self._blocked or any(
            not isinstance(value, str) or len(value) != 64
            or any(char not in "0123456789abcdef" for char in value)
            for value in self._blocked
        ):
            raise ValueError("Lista de bloqueio vazia ou invalida.")

    def validate(self, password, username):
        if not isinstance(password, str) or len(password) < 15:
            raise ValueError("A palavra-passe deve ter pelo menos 15 caracteres.")
        try:
            encoded = password.encode("utf-8")
        except UnicodeEncodeError:
            raise ValueError("Palavra-passe UTF-8 invalida.") from None
        if len(encoded) > passwords.MAX_PASSWORD_BYTES:
            raise ValueError("A palavra-passe excede 4096 bytes.")
        if password.casefold() == username.casefold():
            raise ValueError("A palavra-passe nao pode ser igual ao nome da conta.")
        if hashlib.sha256(encoded).hexdigest() in self._blocked:
            raise ValueError("Escolhe outra palavra-passe: consta da lista de bloqueio.")


class AdminAccountService:
    """OS/database access is the trust boundary; never expose directly as a route."""

    def __init__(self, repo, policy):
        self.repo = repo
        self.policy = policy

    def _user(self, username, expected_version):
        validate_username(username)
        if type(expected_version) is not int or not 1 <= expected_version <= 2**63 - 1:
            raise ValueError("Versao de credenciais invalida.")
        user = self.repo.get_by_username(username)
        if user is None or user.credential_version != expected_version:
            raise AccountConflict("Conta ausente ou alterada; volta a consultar antes de repetir.")
        return user

    def create(self, username, password, role, *, now):
        validate_username(username)
        if role not in ("admin", "viewer"):
            raise ValueError("Papel invalido.")
        if type(now) is not int or not 0 <= now <= 2**63 - 1:
            raise ValueError("Data invalida.")
        self.policy.validate(password, username)
        if self.repo.get_by_username(username) is not None:
            raise AccountConflict("A conta ja existe; nao foi substituida.")
        return self.repo.create_user(username, passwords.hash_password(password), role, now)

    def reset_password(self, username, expected_version, password):
        user = self._user(username, expected_version)
        self.policy.validate(password, username)
        hashed = passwords.hash_password(password)
        self._update(user, hashed, user.role, user.is_active)

    def set_role(self, username, expected_version, role):
        if role not in ("admin", "viewer"):
            raise ValueError("Papel invalido.")
        user = self._user(username, expected_version)
        self._update(user, user.password_hash, role, user.is_active)

    def set_active(self, username, expected_version, active):
        if type(active) is not bool:
            raise ValueError("Estado invalido.")
        user = self._user(username, expected_version)
        self._update(user, user.password_hash, user.role, active)

    def _update(self, user, hashed, role, active):
        if not self.repo.update_user(user.id, user.credential_version, hashed, role, active):
            raise AccountConflict("A conta mudou durante a operacao; nenhuma atualizacao aplicada.")

    def revoke_sessions(self, username):
        validate_username(username)
        user = self.repo.get_by_username(username)
        if user is None:
            raise AccountConflict("Conta inexistente.")
        return self.repo.revoke_user_sessions(user.id)
