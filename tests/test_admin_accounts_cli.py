import hashlib
from types import SimpleNamespace
from io import StringIO

import pytest

from mini_marketo import admin_accounts as cli
from mini_marketo.repositories.access_repository import AccessRepository


SECRET = "uma frase longa de teste local"


class TerminalBuffer(StringIO):
    def isatty(self):
        return True


@pytest.fixture
def interactive(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "sys", SimpleNamespace(
        stdin=SimpleNamespace(isatty=lambda: True),
        stderr=TerminalBuffer(),
    ))
    monkeypatch.setattr("builtins.input", lambda prompt: "ADMIN")
    monkeypatch.setattr(cli, "read_password", lambda: SECRET)
    blocklist = tmp_path / "blocked.sha256"
    blocklist.write_text(hashlib.sha256(b"blocked-test-password").hexdigest() + "\n")
    return str(blocklist)


def test_parser_does_not_echo_accidental_password(capsys):
    with pytest.raises(SystemExit) as result:
        cli.main(["create", "operator", "--password", SECRET])
    assert result.value.code == 2
    captured = capsys.readouterr()
    assert SECRET not in captured.out + captured.err


def test_help_needs_no_database():
    with pytest.raises(SystemExit) as result:
        cli.main(["--help"])
    assert result.value.code == 0


@pytest.mark.parametrize("action,role", [("create", "admin"), ("create", "viewer"), ("set-role", "viewer"), ("reset-password", None), ("disable", None), ("enable", None), ("revoke-sessions", None)])
def test_cli_dispatch_and_closes_connection(monkeypatch, interactive, db, action, role):
    if db.kind == "sqlite":
        repo = AccessRepository(db.conn)
    else:
        from mini_marketo.repositories.postgres_access_repository import PostgresAccessRepository
        repo = PostgresAccessRepository(db.conn)
    if action != "create":
        repo.create_user("operator", "test-hash", "admin", 100)
    closed = []
    monkeypatch.setattr(cli, "open_repository", lambda: (SimpleNamespace(close=lambda: closed.append(True)), repo))
    argv = [action, "operator", "--blocklist", interactive]
    if role:
        argv += ["--role", role]
    assert cli.main(argv) == 0
    assert closed == [True]
    user = repo.get_by_username("operator")
    if action == "create":
        assert user.role == role and user.credential_version == 1
    elif action == "set-role":
        assert user.role == role and user.credential_version == 2
    elif action in ("disable", "enable"):
        assert user.is_active is (action == "enable")


@pytest.mark.parametrize("scenario", ["no-tty", "cancel", "bad-list", "missing-role"])
def test_cli_preflight_does_not_open_database(monkeypatch, interactive, scenario):
    def forbidden():
        raise AssertionError("database should not be opened")
    calls = []
    monkeypatch.setattr(cli, "open_repository", lambda: calls.append(True) or forbidden())
    if scenario == "no-tty":
        monkeypatch.setattr(cli.sys, "stdin", SimpleNamespace(isatty=lambda: False))
    elif scenario == "cancel":
        monkeypatch.setattr("builtins.input", lambda prompt: "NO")
    elif scenario == "bad-list":
        interactive += ".absent"
    argv = ["create", "operator", "--blocklist", interactive]
    if scenario != "missing-role":
        argv += ["--role", "admin"]
    assert cli.main(argv) == 2
    assert not calls


def test_getpass_mismatch_rejected(monkeypatch):
    values = iter([SECRET, SECRET + "different"])
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt: next(values))
    with pytest.raises(ValueError):
        cli.read_password()


def test_getpass_echo_fallback_rejected(monkeypatch):
    import warnings
    def fallback(prompt):
        warnings.warn("no terminal", cli.getpass.GetPassWarning)
        raise AssertionError("must not read with echo")
    monkeypatch.setattr(cli.getpass, "getpass", fallback)
    with pytest.raises(cli.getpass.GetPassWarning):
        cli.read_password()


def test_open_requires_explicit_unambiguous_target(monkeypatch):
    monkeypatch.delenv("MINI_MARKETO_DB", raising=False)
    monkeypatch.delenv("MINI_MARKETO_DATABASE_URL", raising=False)
    with pytest.raises(ValueError):
        cli.open_repository()
    monkeypatch.setenv("MINI_MARKETO_DB", "some.db")
    monkeypatch.setenv("MINI_MARKETO_DATABASE_URL", "postgresql://test")
    with pytest.raises(ValueError):
        cli.open_repository()


def test_open_rejects_missing_sqlite_database(monkeypatch, tmp_path):
    monkeypatch.delenv("MINI_MARKETO_DATABASE_URL", raising=False)
    monkeypatch.setenv("MINI_MARKETO_DB", str(tmp_path / "absent.db"))
    # Stub the connection module to verify this preflight needs no connection.
    import sys
    def forbidden(*args):
        raise AssertionError("must not connect")
    monkeypatch.setitem(sys.modules, "mini_marketo.database", SimpleNamespace(connect=forbidden, init_db=forbidden))
    with pytest.raises(ValueError):
        cli.open_repository()


def test_database_failure_is_sanitized_and_connection_closed(monkeypatch, interactive):
    closed = []
    def fail(*args):
        raise RuntimeError(SECRET + " postgresql://private-dsn")
    repo = SimpleNamespace(get_by_username=fail)
    monkeypatch.setattr(cli, "open_repository", lambda: (SimpleNamespace(close=lambda: closed.append(True)), repo))
    assert cli.main(["reset-password", "operator", "--blocklist", interactive]) == 2
    assert closed == [True]
    output = cli.sys.stderr.getvalue()
    assert SECRET not in output and "private-dsn" not in output


def test_cancelled_password_does_not_create_account(monkeypatch, interactive, db):
    if db.kind == "sqlite":
        repo = AccessRepository(db.conn)
    else:
        from mini_marketo.repositories.postgres_access_repository import PostgresAccessRepository
        repo = PostgresAccessRepository(db.conn)
    closed = []
    monkeypatch.setattr(cli, "open_repository", lambda: (SimpleNamespace(close=lambda: closed.append(True)), repo))
    def cancel():
        raise EOFError()
    monkeypatch.setattr(cli, "read_password", cancel)
    assert cli.main(["create", "operator", "--role", "admin", "--blocklist", interactive]) == 2
    assert closed == [True]
    assert db.scalar("SELECT count(*) FROM admin_users") == 0


def test_initialization_failure_closes_connection(monkeypatch, tmp_path):
    import sys
    path = tmp_path / "existing.db"
    path.touch()
    monkeypatch.delenv("MINI_MARKETO_DATABASE_URL", raising=False)
    monkeypatch.setenv("MINI_MARKETO_DB", str(path))
    closed = []
    def fail(conn):
        raise RuntimeError("migration failed")
    monkeypatch.setitem(sys.modules, "mini_marketo.database", SimpleNamespace(
        connect=lambda path: SimpleNamespace(close=lambda: closed.append(True)), init_db=fail,
    ))
    with pytest.raises(RuntimeError, match="migration failed"):
        cli.open_repository()
    assert closed == [True]
