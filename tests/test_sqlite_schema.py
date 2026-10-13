from mini_marketo.database import connect


def test_connect_enables_foreign_keys():
    conn = connect(":memory:")
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_connect_sets_busy_timeout():
    conn = connect(":memory:")
    assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 30000
