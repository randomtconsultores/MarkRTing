"""Lógica de versões partilhada pelos dois backends (sem SQL).

Cada backend tem uma versão-base (esquema criado de raiz) e uma lista ordenada de
migrações `(versão de destino, script)`. As migrações são consecutivas a partir da base.
"""

from collections.abc import Sequence

Migration = tuple[int, str]


class MigrationError(RuntimeError):
    pass


def latest_version(baseline: int, migrations: Sequence[Migration]) -> int:
    """Versão mais recente; as migrações têm de ser consecutivas a partir da base."""
    expected = baseline
    for target, _ in migrations:
        expected += 1
        if target != expected:
            raise MigrationError(
                f"Migrações fora de ordem: esperada a versão {expected}, encontrada {target}."
            )
    return expected


def pending(version: int, migrations: Sequence[Migration]) -> list[Migration]:
    """Migrações ainda por aplicar a uma base na versão `version`, pela ordem certa."""
    return [m for m in migrations if m[0] > version]
