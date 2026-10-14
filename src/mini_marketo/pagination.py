"""Paginação em memória: pura, sem base de dados nem framework."""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Generic, TypeVar

PAGE_SIZES = (10, 25, 50, 100)
DEFAULT_PAGE_SIZE = 25

T = TypeVar("T")


@dataclass(frozen=True)
class Page(Generic[T]):
    items: list[T]
    page: int
    per_page: int
    total: int

    @property
    def pages(self) -> int:
        return max(1, -(-self.total // self.per_page))

    @property
    def first(self) -> int:
        """Posição (a contar de 1) do primeiro elemento da página; 0 se não houver elementos."""
        return 0 if self.total == 0 else (self.page - 1) * self.per_page + 1

    @property
    def last(self) -> int:
        return (self.page - 1) * self.per_page + len(self.items)

    @property
    def has_previous(self) -> bool:
        return self.page > 1

    @property
    def has_next(self) -> bool:
        return self.page < self.pages


def parse_per_page(raw) -> int:
    """Só aceita os tamanhos da lista; qualquer outro valor volta ao tamanho por omissão."""
    try:
        value = int(str(raw).strip())
    except ValueError:
        return DEFAULT_PAGE_SIZE
    return value if value in PAGE_SIZES else DEFAULT_PAGE_SIZE


def parse_page(raw) -> int:
    try:
        value = int(str(raw).strip())
    except ValueError:
        return 1
    return max(1, value)


def paginate(items: Sequence[T], page: int, per_page: int) -> Page[T]:
    """Devolve uma página; um número de página fora do intervalo é ajustado ao intervalo."""
    if per_page < 1:
        raise ValueError("per_page tem de ser positivo.")
    total = len(items)
    pages = max(1, -(-total // per_page))
    page = min(max(1, page), pages)
    start = (page - 1) * per_page
    return Page(list(items[start : start + per_page]), page, per_page, total)
