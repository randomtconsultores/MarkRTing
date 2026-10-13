from datetime import datetime, timedelta, timezone

import pytest


def test_data_persists_across_connections(db):
    db.service().create_contact("Ana", "Silva", "a@b.pt")
    other = db.reopen()
    try:
        assert [c.email for c in other.service().list_contacts()] == ["a@b.pt"]
    finally:
        other.close()


def test_database_allows_only_one_primary_per_contact(db):
    ana = db.service().create_contact("Ana", "Silva", "ana@a.pt")
    with pytest.raises(db.integrity_error):
        db.execute(
            "INSERT INTO contact_emails (contact_id, email, is_primary) "
            f"VALUES ({db.p}, 'z@a.pt', {db.true})",
            (ana.id,),
        )


def test_check_constraint_rejects_uppercase_email(db):
    ana = db.service().create_contact("Ana", "Silva", "ana@a.pt")
    with pytest.raises(db.integrity_error):
        db.execute(
            f"INSERT INTO contact_emails (contact_id, email) VALUES ({db.p}, 'ANA@B.PT')",
            (ana.id,),
        )


def test_deleting_contact_deletes_its_emails(db):
    service = db.service()
    ana = service.create_contact("Ana", "Silva", "ana@a.pt")
    service.add_email(ana.id, "b@a.pt")
    db.execute(f"DELETE FROM contacts WHERE id = {db.p}", (ana.id,))
    assert db.scalar("SELECT COUNT(*) FROM contact_emails") == 0


def test_created_at_is_naive_utc(db):
    c = db.service().create_contact("Ana", "Silva", "ana@a.pt")
    assert c.created_at.tzinfo is None
    now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
    assert abs(now_utc - c.created_at) < timedelta(seconds=30)


def test_foreign_key_rejects_unknown_company(db):
    with pytest.raises(db.integrity_error):
        db.execute(
            "INSERT INTO contacts (first_name, last_name, company_id) VALUES ('A', 'B', 9999)"
        )


def test_company_name_is_unique_ignoring_case(db):
    db.execute("INSERT INTO companies (name) VALUES ('ACME')")
    with pytest.raises(db.integrity_error):
        db.execute("INSERT INTO companies (name) VALUES ('acme')")
