import pytest

from mini_marketo.migrations import MigrationError, latest_version, pending


def test_latest_version_without_migrations_is_the_baseline():
    assert latest_version(2, []) == 2


def test_latest_version_follows_consecutive_migrations():
    assert latest_version(2, [(3, "a"), (4, "b")]) == 4


@pytest.mark.parametrize(
    "bad",
    [[(4, "a")], [(3, "a"), (3, "b")], [(3, "a"), (5, "b")], [(2, "a")]],
)
def test_non_consecutive_migrations_are_rejected(bad):
    with pytest.raises(MigrationError):
        latest_version(2, bad)


def test_pending_returns_only_newer_migrations_in_order():
    migrations = [(3, "a"), (4, "b"), (5, "c")]
    assert pending(3, migrations) == [(4, "b"), (5, "c")]
    assert pending(5, migrations) == []


def test_both_backends_declare_the_same_schema_version():
    pytest.importorskip("psycopg")
    from mini_marketo import database, postgres_database

    assert database.SCHEMA_VERSION == postgres_database.SCHEMA_VERSION
