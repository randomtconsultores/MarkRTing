import pytest
from argon2 import PasswordHasher, extract_parameters
from argon2.exceptions import HashingError
from argon2.low_level import Type

from mini_marketo import passwords


def test_roundtrip_and_parameters():
    stored = passwords.hash_password("uma frase de teste")
    assert passwords.verify_password(stored, "uma frase de teste")
    assert not passwords.verify_password(stored, "outra frase")
    assert not passwords.password_needs_rehash(stored)
    params = extract_parameters(stored)
    assert params.type is Type.ID
    assert (params.memory_cost, params.time_cost, params.parallelism) == (19456, 2, 1)
    assert (params.salt_len, params.hash_len) == (16, 32)


def test_random_salt():
    first = passwords.hash_password("repetida")
    second = passwords.hash_password("repetida")
    assert first != second
    assert passwords.verify_password(first, "repetida")
    assert passwords.verify_password(second, "repetida")


@pytest.mark.parametrize("value", ["  espacos  ", "acao-ç-🔐", "abc\x00def", "é" * 2048])
def test_input_preserved(value):
    stored = passwords.hash_password(value)
    assert passwords.verify_password(stored, value)
    assert not passwords.verify_password(stored, value + "x")


def test_no_unicode_normalization_or_trimming():
    stored = passwords.hash_password(" é ")
    assert not passwords.verify_password(stored, "é")
    assert not passwords.verify_password(stored, " e\u0301 ")


@pytest.mark.parametrize("value", [None, 123, b"bytes", "", "x" * 4097, "é" * 2049, "\ud800"])
def test_invalid_password(value):
    with pytest.raises(ValueError):
        passwords.hash_password(value)
    assert not passwords.verify_password("$argon2id$invalid", value)


@pytest.mark.parametrize("stored", [None, 12, b"hash", "", "plaintext", "$argon2id$invalid"])
def test_invalid_hash(stored):
    assert not passwords.verify_password(stored, "test")
    with pytest.raises(ValueError):
        passwords.password_needs_rehash(stored)


def test_changed_parameters_detected():
    old = PasswordHasher(time_cost=1, memory_cost=19456, parallelism=1).hash("test")
    assert passwords.verify_password(old, "test")
    assert passwords.password_needs_rehash(old)


def test_other_argon2_variant_rejected():
    other = PasswordHasher(time_cost=2, memory_cost=19456, parallelism=1, type=Type.I).hash("test")
    assert not passwords.verify_password(other, "test")
    with pytest.raises(ValueError):
        passwords.password_needs_rehash(other)


def test_hashing_failure_propagates(monkeypatch):
    def fail(*args, **kwargs):
        raise HashingError("failure")
    monkeypatch.setattr(PasswordHasher, "hash", fail)
    with pytest.raises(HashingError):
        passwords.hash_password("test")
