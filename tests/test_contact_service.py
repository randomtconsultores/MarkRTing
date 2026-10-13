import pytest

from mini_marketo.database import connect, init_db
from mini_marketo.domain.contact import DuplicateEmailError, ValidationError, normalize_email
from mini_marketo.repositories.contact_repository import ContactRepository
from mini_marketo.services.contact_service import ContactService


def make_service(path=":memory:"):
    conn = connect(path)
    init_db(conn)
    return ContactService(ContactRepository(conn))


@pytest.fixture
def service():
    return make_service()


def test_normalizes_email(service):
    c = service.create_contact("Ana", "Silva", "  ANA@Example.COM ")
    assert c.email == "ana@example.com"


def test_normalize_email_function():
    assert normalize_email("  X@Y.Z ") == "x@y.z"


@pytest.mark.parametrize("bad", ["", "abc", "a@b", "a b@c.com", "@c.com", "a@@c.com"])
def test_invalid_email(service, bad):
    with pytest.raises(ValidationError):
        service.create_contact("Ana", "Silva", bad)


def test_duplicate_email_is_case_and_space_insensitive(service):
    service.create_contact("Ana", "Silva", "ana@example.com")
    with pytest.raises(DuplicateEmailError):
        service.create_contact("Outra", "Pessoa", " ANA@example.com ")
    assert len(service.list_contacts()) == 1


@pytest.mark.parametrize("first,last", [("", "Silva"), ("Ana", "  "), ("x" * 101, "S")])
def test_invalid_names(service, first, last):
    with pytest.raises(ValidationError):
        service.create_contact(first, last, "a@b.pt")


def test_contact_with_company(service):
    co = service.create_company("ACME", "https://acme.pt")
    c = service.create_contact("Ana", "Silva", "a@acme.pt", co.id)
    assert c.company_name == "ACME"


def test_unknown_company_rejected(service):
    with pytest.raises(ValidationError):
        service.create_contact("Ana", "Silva", "a@b.pt", company_id=999)


def test_duplicate_company_name_rejected(service):
    service.create_company("ACME")
    with pytest.raises(ValidationError):
        service.create_company("acme")


def test_data_persists_after_reopen(tmp_path):
    db = tmp_path / "t.db"
    make_service(db).create_contact("Ana", "Silva", "a@b.pt")
    assert [c.email for c in make_service(db).list_contacts()] == ["a@b.pt"]
