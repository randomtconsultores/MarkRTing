from datetime import datetime, timedelta, timezone

import pytest

psycopg = pytest.importorskip("psycopg")

from mini_marketo.postgres_database import SCHEMA_VERSION, connect, init_db  # noqa: E402
from mini_marketo.repositories.postgres_contact_repository import (  # noqa: E402
    PostgresContactRepository,
)
from mini_marketo.services.contact_service import ContactService  # noqa: E402


@pytest.fixture
def conn(pg_url, pg_schema):
    c = connect(pg_url, pg_schema)
    init_db(c)
    yield c
    c.close()


def make_service(conn):
    return ContactService(PostgresContactRepository(conn))


def test_schema_version_recorded_and_init_is_idempotent(conn):
    init_db(conn)
    init_db(conn)
    rows = conn.execute("SELECT version FROM schema_version").fetchall()
    assert rows == [{"version": SCHEMA_VERSION}]


def test_data_persists_across_connections(pg_url, pg_schema, conn):
    make_service(conn).create_contact("Ana", "Silva", "a@b.pt")
    other = connect(pg_url, pg_schema)
    try:
        assert [c.email for c in make_service(other).list_contacts()] == ["a@b.pt"]
    finally:
        other.close()


def test_database_allows_only_one_primary_per_contact(conn):
    ana = make_service(conn).create_contact("Ana", "Silva", "ana@a.pt")
    with pytest.raises(psycopg.errors.UniqueViolation):
        conn.execute(
            "INSERT INTO contact_emails (contact_id, email, is_primary) VALUES (%s, 'z@a.pt', TRUE)",
            (ana.id,),
        )


def test_check_constraint_rejects_uppercase_email(conn):
    ana = make_service(conn).create_contact("Ana", "Silva", "ana@a.pt")
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute(
            "INSERT INTO contact_emails (contact_id, email) VALUES (%s, 'ANA@B.PT')",
            (ana.id,),
        )


def test_deleting_contact_deletes_its_emails(conn):
    service = make_service(conn)
    ana = service.create_contact("Ana", "Silva", "ana@a.pt")
    service.add_email(ana.id, "b@a.pt")
    conn.execute("DELETE FROM contacts WHERE id = %s", (ana.id,))
    n = conn.execute("SELECT count(*) AS n FROM contact_emails").fetchone()["n"]
    assert n == 0


def test_created_at_is_naive_utc(conn):
    c = make_service(conn).create_contact("Ana", "Silva", "ana@a.pt")
    assert c.created_at.tzinfo is None
    now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
    assert abs(now_utc - c.created_at) < timedelta(seconds=30)
