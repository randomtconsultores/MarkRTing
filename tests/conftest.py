import os
import sqlite3
import uuid

import pytest

from mini_marketo import database as sqlite_database
from mini_marketo.database import connect as sqlite_connect
from mini_marketo.database import init_db as sqlite_init_db
from mini_marketo.repositories.consent_repository import ConsentRepository
from mini_marketo.repositories.contact_repository import ContactRepository
from mini_marketo.repositories.segment_repository import SegmentRepository
from mini_marketo.services.contact_service import ContactService
from mini_marketo.services.segment_service import SegmentService


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


class Db:
    """Adaptador mínimo para testes ao nível do esquema: o mesmo teste corre nos dois backends."""

    def __init__(self, kind, conn, module, integrity_error, reopen):
        self.kind = kind
        self.conn = conn
        self.module = module
        self.integrity_error = integrity_error
        self._reopen = reopen
        self.p = "?" if kind == "sqlite" else "%s"
        self.true = "1" if kind == "sqlite" else "TRUE"

    def execute(self, sql, params=()):
        if self.kind == "sqlite":
            try:
                cur = self.conn.execute(sql, params)
            except Exception:
                self.conn.rollback()
                raise
            self.conn.commit()
            return cur
        return self.conn.execute(sql, params or None)

    def scalar(self, sql, params=()):
        row = self.execute(sql, params).fetchone()
        return row[0] if self.kind == "sqlite" else next(iter(row.values()))

    def version(self):
        if self.kind == "sqlite":
            return self.scalar("PRAGMA user_version")
        return self.scalar("SELECT max(version) FROM schema_version")

    def set_version(self, number):
        if self.kind == "sqlite":
            self.execute(f"PRAGMA user_version = {int(number)}")
        else:
            self.execute("UPDATE schema_version SET version = %s", (number,))

    def service(self):
        if self.kind == "sqlite":
            return ContactService(ContactRepository(self.conn))
        from mini_marketo.repositories.postgres_contact_repository import (
            PostgresContactRepository,
        )

        return ContactService(PostgresContactRepository(self.conn))

    def segment_service(self):
        if self.kind == "sqlite":
            return SegmentService(
                SegmentRepository(self.conn),
                ContactRepository(self.conn),
                ConsentRepository(self.conn),
            )
        from mini_marketo.repositories.postgres_consent_repository import (
            PostgresConsentRepository,
        )
        from mini_marketo.repositories.postgres_contact_repository import (
            PostgresContactRepository,
        )
        from mini_marketo.repositories.postgres_segment_repository import (
            PostgresSegmentRepository,
        )

        return SegmentService(
            PostgresSegmentRepository(self.conn),
            PostgresContactRepository(self.conn),
            PostgresConsentRepository(self.conn),
        )

    def consent_service(self):
        from mini_marketo.services.consent_service import ConsentService

        if self.kind == "sqlite":
            return ConsentService(ConsentRepository(self.conn), ContactRepository(self.conn))
        from mini_marketo.repositories.postgres_consent_repository import (
            PostgresConsentRepository,
        )
        from mini_marketo.repositories.postgres_contact_repository import (
            PostgresContactRepository,
        )

        return ConsentService(
            PostgresConsentRepository(self.conn), PostgresContactRepository(self.conn)
        )

    def reopen(self):
        return self._reopen()

    def close(self):
        self.conn.close()


@pytest.fixture(params=["sqlite", "postgres"])
def db(request, tmp_path):
    if request.param == "sqlite":
        path = tmp_path / "schema.db"

        def open_sqlite():
            conn = sqlite_connect(path)
            sqlite_init_db(conn)
            return Db("sqlite", conn, sqlite_database, sqlite3.IntegrityError, open_sqlite)

        handle = open_sqlite()
    else:
        url = request.getfixturevalue("pg_url")
        schema = request.getfixturevalue("pg_schema")
        import psycopg

        from mini_marketo import postgres_database as pg_database

        def open_postgres():
            conn = pg_database.connect(url, schema)
            pg_database.init_db(conn)
            return Db("postgres", conn, pg_database, psycopg.errors.IntegrityError, open_postgres)

        handle = open_postgres()
    yield handle
    handle.close()
