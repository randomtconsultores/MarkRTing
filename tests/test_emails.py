import sqlite3

import pytest

from mini_marketo.database import connect, init_db
from mini_marketo.domain.contact import DuplicateEmailError, ValidationError
from mini_marketo.repositories.contact_repository import ContactRepository
from mini_marketo.services.contact_service import ContactService


@pytest.fixture
def service():
    conn = connect(":memory:")
    init_db(conn)
    return ContactService(ContactRepository(conn))


@pytest.fixture
def ana(service):
    return service.create_contact("Ana", "Silva", "ana@a.pt")


def test_add_secondary_email_keeps_primary(service, ana):
    c = service.add_email(ana.id, "  ANA.Work@A.pt ")
    assert c.emails == ("ana@a.pt", "ana.work@a.pt")
    assert c.email == "ana@a.pt"


def test_invalid_secondary_email(service, ana):
    with pytest.raises(ValidationError):
        service.add_email(ana.id, "abc")


def test_email_unique_across_contacts(service, ana):
    rui = service.create_contact("Rui", "Costa", "rui@a.pt")
    with pytest.raises(DuplicateEmailError):
        service.add_email(rui.id, "ANA@a.pt")
    service.add_email(ana.id, "extra@a.pt")
    with pytest.raises(DuplicateEmailError):
        service.create_contact("Outro", "X", "extra@a.pt")


def test_same_email_twice_on_same_contact_rejected(service, ana):
    with pytest.raises(DuplicateEmailError):
        service.add_email(ana.id, "ana@a.pt")


def test_add_email_unknown_contact(service):
    with pytest.raises(ValidationError):
        service.add_email(999, "x@a.pt")


def test_set_primary_email(service, ana):
    service.add_email(ana.id, "b@a.pt")
    c = service.set_primary_email(ana.id, "B@a.pt")
    assert c.emails == ("b@a.pt", "ana@a.pt")


def test_set_primary_foreign_email_rejected_and_state_unchanged(service, ana):
    rui = service.create_contact("Rui", "Costa", "rui@a.pt")
    with pytest.raises(ValidationError):
        service.set_primary_email(ana.id, "rui@a.pt")
    assert service.repo.get_contact(ana.id).emails == ("ana@a.pt",)
    assert service.repo.get_contact(rui.id).emails == ("rui@a.pt",)


def test_remove_secondary_email(service, ana):
    service.add_email(ana.id, "b@a.pt")
    c = service.remove_email(ana.id, "b@a.pt")
    assert c.emails == ("ana@a.pt",)


def test_cannot_remove_primary_email(service, ana):
    service.add_email(ana.id, "b@a.pt")
    with pytest.raises(ValidationError):
        service.remove_email(ana.id, "ana@a.pt")
    assert service.repo.get_contact(ana.id).emails == ("ana@a.pt", "b@a.pt")


def test_cannot_remove_only_email(service, ana):
    with pytest.raises(ValidationError):
        service.remove_email(ana.id, "ana@a.pt")


def test_remove_unknown_email(service, ana):
    with pytest.raises(ValidationError):
        service.remove_email(ana.id, "nao@a.pt")


def test_database_allows_only_one_primary_per_contact(service, ana):
    with pytest.raises(sqlite3.IntegrityError):
        service.repo.conn.execute(
            "INSERT INTO contact_emails (contact_id, email, is_primary) VALUES (?, 'z@a.pt', 1)",
            (ana.id,),
        )


def test_deleting_contact_deletes_its_emails(service, ana):
    service.add_email(ana.id, "b@a.pt")
    with service.repo.conn:
        service.repo.conn.execute("DELETE FROM contacts WHERE id = ?", (ana.id,))
    n = service.repo.conn.execute("SELECT COUNT(*) FROM contact_emails").fetchone()[0]
    assert n == 0


def test_list_contacts_includes_all_emails(service, ana):
    service.add_email(ana.id, "b@a.pt")
    service.create_contact("Rui", "Costa", "rui@a.pt")
    by_name = {c.first_name: c.emails for c in service.list_contacts()}
    assert by_name == {"Ana": ("ana@a.pt", "b@a.pt"), "Rui": ("rui@a.pt",)}
