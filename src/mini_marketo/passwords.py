"""Password hashing only; no login, persistence or account policy.

Stored hashes must come from trusted application storage, never request data.
The caller must rate-limit authentication and avoid blocking the async event loop.
"""

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from argon2.low_level import Type

MAX_PASSWORD_BYTES = 4096
_HASHER = PasswordHasher(
    time_cost=2, memory_cost=19456, parallelism=1,
    hash_len=32, salt_len=16, type=Type.ID,
)


def _password_bytes(password: str) -> bytes:
    if not isinstance(password, str):
        raise ValueError("A palavra-passe tem de ser texto UTF-8 nao vazio.")
    try:
        encoded = password.encode("utf-8")
    except UnicodeEncodeError:
        raise ValueError("A palavra-passe tem de ser texto UTF-8 valido.") from None
    if not 1 <= len(encoded) <= MAX_PASSWORD_BYTES:
        raise ValueError("A palavra-passe excede os limites de tamanho.")
    return encoded


def hash_password(password: str) -> str:
    """Hash with a fresh library-generated salt; do not trim or normalize input.

    Minimum length and compromised-password checks belong to account creation.
    Hashing failures propagate: no insecure fallback is attempted.
    """
    return _HASHER.hash(_password_bytes(password))


def verify_password(stored_hash: str, password: str) -> bool:
    """Reject invalid input, unsupported hashes and mismatches without logging secrets.

    This is not a complete authentication flow or a timing-equalization mechanism.
    """
    try:
        encoded = _password_bytes(password)
    except ValueError:
        return False
    if not isinstance(stored_hash, str) or not stored_hash.startswith("$argon2id$"):
        return False
    try:
        return _HASHER.verify(stored_hash, encoded)
    except (InvalidHashError, VerificationError):
        return False


def password_needs_rehash(stored_hash: str) -> bool:
    """Call only after successful verification; this never updates persistence.

    The future authentication service must coordinate any update with the
    repository's credential version and session revocation rules.
    """
    if not isinstance(stored_hash, str) or not stored_hash.startswith("$argon2id$"):
        raise ValueError("Esperado um hash Argon2id verificado.")
    try:
        return _HASHER.check_needs_rehash(stored_hash)
    except (InvalidHashError, ValueError):
        raise ValueError("Esperado um hash Argon2id verificado.") from None
