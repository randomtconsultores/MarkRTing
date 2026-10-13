import pytest

psycopg = pytest.importorskip("psycopg")

from mini_marketo.postgres_database import connect, init_db  # noqa: E402


@pytest.fixture
def conn(pg_url, pg_schema):
    c = connect(pg_url, pg_schema)
    init_db(c)
    yield c
    c.close()


def test_ids_are_identity_columns(conn):
    row = conn.execute(
        "SELECT is_identity FROM information_schema.columns "
        "WHERE table_schema = current_schema() AND table_name = 'contacts' AND column_name = 'id'"
    ).fetchone()
    assert row["is_identity"] == "YES"


def test_schema_version_table_has_a_single_row(conn):
    init_db(conn)
    assert conn.execute("SELECT count(*) AS n FROM schema_version").fetchone()["n"] == 1


def test_connection_is_autocommit(conn):
    assert conn.autocommit
