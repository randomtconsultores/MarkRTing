"""Bounded access-data cleanup: python -m mini_marketo.admin_maintenance."""

import argparse
import re
import sys
import time

from .admin_accounts import open_repository
from .repositories.access_repository import validate_login_attempt_cleanup


class SafeParser(argparse.ArgumentParser):
    def error(self, message):
        self.exit(2, "Argumentos invalidos. Consulta --help.\n")


def main(argv=None):
    parser = SafeParser(prog="python -m mini_marketo.admin_maintenance")
    parser.add_argument("--batch-size", default="1000")
    args = parser.parse_args(argv)
    if re.fullmatch(r"[0-9]{1,5}", args.batch_size) is None:
        print("Limite de lote invalido.", file=sys.stderr)
        return 2
    batch_size = int(args.batch_size)
    try:
        validate_login_attempt_cleanup(int(time.time()), batch_size)
        conn, repo = open_repository()
        try:
            now = int(time.time())
            sessions = repo.cleanup_expired_sessions(now, batch_size=batch_size)
            attempts = repo.cleanup_expired_login_attempts(now, batch_size=batch_size)
        finally:
            conn.close()
    except Exception:
        print("Limpeza nao concluida; verifica a configuracao e a base de dados.", file=sys.stderr)
        return 2
    print(f"Sessoes expiradas removidas: {sessions}; limites expirados removidos: {attempts}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
