from datetime import datetime

import pytest

from mini_marketo.domain.contact import ValidationError
from mini_marketo.domain.suppression import SuppressionNotFoundError, SuppressionNotRemovableError
from mini_marketo.services.suppression_service import SuppressionService


def make_service(handle):
    if handle.kind == "sqlite":
        from mini_marketo.repositories.suppression_repository import SuppressionRepository

        return SuppressionService(SuppressionRepository(handle.conn))
    from mini_marketo.repositories.postgres_suppression_repository import (
        PostgresSuppressionRepository,
    )

    return SuppressionService(PostgresSuppressionRepository(handle.conn))


def count(handle):
    return handle.scalar("SELECT count(*) FROM suppressions")


def raw_insert(handle, email, reason, source):
    p = handle.p
    handle.execute(
        f"INSERT INTO suppressions (email, reason, source) VALUES ({p}, {p}, {p})",
        (email, reason, source),
    )


def test_add_normalizes_and_lookup_ignores_case(db):
    svc = make_service(db)
    s = svc.add("  Ana@A.PT ", "hard_bounce", "smtp", "550 5.1.1")
    assert (s.email, s.reason, s.source, s.detail) == ("ana@a.pt", "hard_bounce", "smtp", "550 5.1.1")
    assert isinstance(s.created_at, datetime)
    assert svc.is_suppressed("ANA@a.pt")
    assert not svc.is_suppressed("rui@a.pt")
    assert not svc.is_suppressed("")


def test_add_is_idempotent_and_keeps_the_original_record(db):
    svc = make_service(db)
    first = svc.add("a@a.pt", "hard_bounce", "smtp", "primeiro")
    second = svc.add("a@a.pt", "hard_bounce", "event", "segundo")
    assert count(db) == 1
    assert second == first


def test_a_complaint_or_bounce_promotes_a_manual_suppression(db):
    svc = make_service(db)
    svc.add("a@a.pt", "manual", "admin", "a pedido")
    promoted = svc.add("a@a.pt", "complaint", "event", "queixa")
    assert (promoted.reason, promoted.source) == ("complaint", "event")
    with pytest.raises(SuppressionNotRemovableError):
        svc.remove("a@a.pt")
    assert svc.is_suppressed("a@a.pt")


@pytest.mark.parametrize("reason,source", [("hard_bounce", "smtp"), ("complaint", "event")])
def test_a_manual_entry_never_downgrades_an_existing_one(db, reason, source):
    svc = make_service(db)
    svc.add("a@a.pt", reason, source, "")
    again = svc.add("a@a.pt", "manual", "admin", "")
    assert again.reason == reason
    with pytest.raises(SuppressionNotRemovableError):
        svc.remove("a@a.pt")


def test_the_first_non_manual_reason_is_kept(db):
    svc = make_service(db)
    svc.add("a@a.pt", "complaint", "event", "")
    assert svc.add("a@a.pt", "hard_bounce", "smtp", "").reason == "complaint"


def test_a_manual_suppression_can_be_removed_and_added_again(db):
    svc = make_service(db)
    svc.add("a@a.pt", "manual", "admin", "")
    svc.remove("  A@a.pt ")
    assert not svc.is_suppressed("a@a.pt")
    assert count(db) == 0
    svc.add("a@a.pt", "manual", "admin", "")
    assert svc.is_suppressed("a@a.pt")


def test_removing_a_bounce_or_a_missing_address_fails_without_changes(db):
    svc = make_service(db)
    svc.add("a@a.pt", "hard_bounce", "smtp", "")
    with pytest.raises(SuppressionNotRemovableError):
        svc.remove("a@a.pt")
    with pytest.raises(SuppressionNotFoundError):
        svc.remove("nao@existe.pt")
    assert count(db) == 1


def test_list_is_newest_first_and_emails_returns_a_set(db):
    svc = make_service(db)
    for email in ("a@a.pt", "b@a.pt", "c@a.pt"):
        svc.add(email, "hard_bounce", "smtp", "")
    assert [s.email for s in svc.list()] == ["c@a.pt", "b@a.pt", "a@a.pt"]
    assert svc.suppressed_emails() == {"a@a.pt", "b@a.pt", "c@a.pt"}


def test_invalid_input_writes_nothing(db):
    svc = make_service(db)
    for args in (("sem-arroba", "manual", "admin"), ("a@a.pt", "robo", "admin"), ("a@a.pt", "manual", "smtp")):
        with pytest.raises(ValidationError):
            svc.add(*args)
    assert count(db) == 0


def test_the_schema_enforces_its_own_rules(db):
    raw_insert(db, "a@a.pt", "hard_bounce", "smtp")
    for args in (
        ("a@a.pt", "hard_bounce", "smtp"),  # duplicado
        ("B@a.pt", "hard_bounce", "smtp"),  # não normalizado
        ("b@a.pt", "robo", "smtp"),  # motivo desconhecido
        ("b@a.pt", "hard_bounce", "robo"),  # origem desconhecida
        ("b@a.pt", "manual", "event"),  # manual só com admin
        ("b@a.pt", "complaint", "admin"),  # admin só com manual
    ):
        with pytest.raises(db.integrity_error):
            raw_insert(db, *args)
    assert count(db) == 1


def test_migration_v7_creates_the_table_and_keeps_existing_data(db):
    db.service().create_contact("Ana", "Silva", "ana@a.pt")
    db.execute("DROP TABLE suppressions")
    db.set_version(6)
    again = db.reopen()
    try:
        assert again.module.SCHEMA_VERSION >= 7
        assert again.version() == again.module.SCHEMA_VERSION
        assert again.scalar("SELECT count(*) FROM contact_emails") == 1
        svc = make_service(again)
        svc.add("a@a.pt", "hard_bounce", "smtp", "")
        assert svc.is_suppressed("a@a.pt")
    finally:
        again.close()
