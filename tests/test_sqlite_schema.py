import sqlite3

import pytest

from mini_marketo.database import connect, init_db
from mini_marketo.repositories.contact_repository import ContactRepository
from mini_marketo.services.contact_service import ContactService


def make_service(path=":memory:"):
    conn = connect(path)
    init_db(conn)
    return ContactService(ContactRepository(conn))


def test_data_persists_after_reopen(tmp_path):
    db = tmp_path / "t.db"
    make_service(db).create_contact("Ana", "Silva", "a@b.pt")
    assert [c.email for c in make_service(db).list_contacts()] == ["a@b.pt"]


def test_database_allows_only_one_primary_per_contact():
    service = make_service()
    ana = service.create_contact("Ana", "Silva", "ana@a.pt")
    with pytest.raises(sqlite3.IntegrityError):
        service.repo.conn.execute(
            "INSERT INTO contact_emails (contact_id, email, is_primary) VALUES (?, 'z@a.pt', 1)",
            (ana.id,),
        )


def test_deleting_contact_deletes_its_emails():
    service = make_service()
    ana = service.create_contact("Ana", "Silva", "ana@a.pt")
    service.add_email(ana.id, "b@a.pt")
    with service.repo.conn:
        service.repo.conn.execute("DELETE FROM contacts WHERE id = ?", (ana.id,))
    n = service.repo.conn.execute("SELECT COUNT(*) FROM contact_emails").fetchone()[0]
    assert n == 0
