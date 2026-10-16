"""Local maintenance: python -m mini_marketo.admin_accounts --help."""

import argparse
import getpass
import os
from pathlib import Path
import sys
import time
import warnings

from .services.admin_account_service import AdminAccountService, PasswordPolicy, validate_username


class SafeParser(argparse.ArgumentParser):
    def error(self, message):
        # Do not echo unknown arguments that might accidentally contain a password.
        self.exit(2, "Argumentos invalidos. Consulta --help; nunca passes palavras-passe em argumentos.\n")


def open_repository():
    database_url = os.environ.get("MINI_MARKETO_DATABASE_URL", "")
    db_path = os.environ.get("MINI_MARKETO_DB", "")
    if bool(database_url) == bool(db_path):
        raise ValueError("Define exatamente uma base de destino explicitamente.")
    if database_url:
        from .postgres_database import connect, init_db
        from .repositories.postgres_access_repository import PostgresAccessRepository
        conn = connect(database_url)
        initialize, repository = init_db, PostgresAccessRepository
    else:
        from .database import connect, init_db
        from .repositories.access_repository import AccessRepository
        path = Path(db_path)
        if not path.is_file():
            raise ValueError("A base SQLite indicada tem de existir.")
        conn = connect(path)
        initialize, repository = init_db, AccessRepository
    try:
        initialize(conn)
        return conn, repository(conn)
    except BaseException:
        conn.close()
        raise


def read_password():
    with warnings.catch_warnings():
        warnings.simplefilter("error", getpass.GetPassWarning)
        first = getpass.getpass("Nova palavra-passe: ")
        second = getpass.getpass("Confirmar palavra-passe: ")
    if first != second:
        raise ValueError("As palavras-passe nao coincidem.")
    return first


def main(argv=None):
    parser = SafeParser(prog="python -m mini_marketo.admin_accounts")
    parser.add_argument("action", choices=("create", "reset-password", "set-role", "enable", "disable", "revoke-sessions"))
    parser.add_argument("username")
    parser.add_argument("--role", choices=("admin", "viewer"))
    parser.add_argument("--blocklist", required=True, help="ficheiro local: um digest SHA-256 por linha")
    args = parser.parse_args(argv)
    conn = None
    try:
        validate_username(args.username)
        if not sys.stdin.isatty() or not sys.stderr.isatty():
            raise ValueError("E obrigatorio um terminal interativo.")
        if args.action in ("create", "set-role") and args.role is None:
            raise ValueError("Indica --role explicitamente.")
        if args.action not in ("create", "set-role") and args.role is not None:
            raise ValueError("--role nao se aplica a esta operacao.")
        # Blocklists are public lookup data, never account hashes or secrets.
        with open(args.blocklist, encoding="ascii") as source:
            policy = PasswordPolicy(line.rstrip("\r\n") for line in source)
        if input("Confirma acesso administrativo local autorizado (escreve ADMIN): ") != "ADMIN":
            raise ValueError("Operacao cancelada.")
        conn, repo = open_repository()
        service = AdminAccountService(repo, policy)
        if args.action == "create":
            service.create(args.username, read_password(), args.role, now=int(time.time()))
        elif args.action == "revoke-sessions":
            service.revoke_sessions(args.username)
        else:
            user = repo.get_by_username(args.username)
            if user is None:
                raise ValueError("Conta inexistente.")
            if args.action == "reset-password":
                service.reset_password(args.username, user.credential_version, read_password())
            elif args.action == "set-role":
                service.set_role(args.username, user.credential_version, args.role)
            else:
                service.set_active(args.username, user.credential_version, args.action == "enable")
        print("Operacao administrativa concluida.")
        return 0
    except (EOFError, KeyboardInterrupt, getpass.GetPassWarning):
        print("Operacao cancelada; nenhum segredo foi apresentado.", file=sys.stderr)
        return 2
    except Exception:
        # Do not print SQL parameters, DSNs, passwords, hashes or exception reprs.
        print("Operacao nao concluida. Verifica configuracao, politica, conta e versao; nao ha repeticao automatica.", file=sys.stderr)
        return 2
    finally:
        if conn is not None:
            conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
