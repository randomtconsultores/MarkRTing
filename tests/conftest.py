import os
import uuid

import pytest

from mini_marketo.database import connect as sqlite_connect
from mini_marketo.database import init_db as sqlite_init_db
from mini_marketo.repositories.contact_repository import ContactRepository
from mini_marketo.services.contact_service import ContactService


@pytest.fixture
def pg_url():
    url = os.environ.get("MINI_MARKETO_TEST_PG")
    if not url:
        pytest.skip("MINI_MARKETO_TEST_PG não definido")
    pytest.importorskip("psycopg")
    return url


@pytest.fixture
def pg_schema(pg_url):
    import psycopg

    name = f"t_{uuid.uuid4().hex[:12]}"
    with psycopg.connect(pg_url, autocommit=True) as admin:
        admin.execute(f'CREATE SCHEMA "{name}"')
    try:
        yield name
    finally:
        with psycopg.connect(pg_url, autocommit=True) as admin:
            admin.execute(f'DROP SCHEMA "{name}" CASCADE')


@pytest.fixture(params=["sqlite", "postgres"])
def service(request):
    """O mesmo conjunto de testes corre contra os dois repositórios."""
    if request.param == "sqlite":
        conn = sqlite_connect(":memory:")
        sqlite_init_db(conn)
        yield ContactService(ContactRepository(conn))
        conn.close()
    else:
        url = request.getfixturevalue("pg_url")
        schema = request.getfixturevalue("pg_schema")
        from mini_marketo.postgres_database import connect, init_db
        from mini_marketo.repositories.postgres_contact_repository import (
            PostgresContactRepository,
        )

        conn = connect(url, schema)
        init_db(conn)
        yield ContactService(PostgresContactRepository(conn))
        conn.close()
