"""Textos das mensagens e páginas públicas, num ficheiro por idioma (`texts/<idioma>.json`).

Cada ficheiro é um objeto JSON de chave -> texto. Os marcadores são `{nome}`; um `{` ou `}` literal
escreve-se `{{` e `}}`. O idioma por omissão (`pt`) tem de existir e é o de recurso: um idioma
desconhecido, ou uma chave que falte noutro idioma, usa o texto do idioma por omissão.
"""

import json
from functools import lru_cache
from importlib import resources
from string import Formatter

from .domain.subscription import DEFAULT_LANG


@lru_cache(maxsize=1)
def load_catalog() -> dict[str, dict[str, str]]:
    catalog: dict[str, dict[str, str]] = {}
    for entry in resources.files("mini_marketo").joinpath("texts").iterdir():
        if not entry.name.endswith(".json"):
            continue
        data = json.loads(entry.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in data.items()
        ):
            raise ValueError(f"texts/{entry.name}: tem de ser um objeto de chave -> texto.")
        catalog[entry.name[: -len(".json")]] = data
    if DEFAULT_LANG not in catalog:
        raise ValueError(f"Falta texts/{DEFAULT_LANG}.json (o idioma por omissão).")
    return catalog


def available_languages() -> tuple[str, ...]:
    return tuple(sorted(load_catalog()))


def placeholders(template: str) -> set[str]:
    return {name for _, name, _, _ in Formatter().parse(template) if name}


def text(lang: str, key: str, **params) -> str:
    """O texto da chave no idioma pedido (ou no por omissão), com os marcadores preenchidos.

    Uma chave que não exista no idioma por omissão, ou um marcador sem valor, é um erro de
    programação e levanta KeyError.
    """
    catalog = load_catalog()
    template = catalog.get(lang, {}).get(key)
    if template is None:
        template = catalog[DEFAULT_LANG][key]
    return template.format(**params)


def catalog_problems(catalog: dict[str, dict[str, str]]) -> list[str]:
    """Diferenças entre cada idioma e o por omissão: chaves em falta ou a mais e marcadores
    diferentes. Uma lista vazia significa que o catálogo está coerente."""
    if DEFAULT_LANG not in catalog:
        return [f"Falta o idioma por omissão ({DEFAULT_LANG})."]
    base = catalog[DEFAULT_LANG]
    problems = []
    for lang, table in sorted(catalog.items()):
        if lang == DEFAULT_LANG:
            continue
        for key in sorted(set(base) - set(table)):
            problems.append(f"{lang}: falta a chave {key}.")
        for key in sorted(set(table) - set(base)):
            problems.append(f"{lang}: chave a mais {key}.")
        for key in sorted(set(base) & set(table)):
            if placeholders(base[key]) != placeholders(table[key]):
                problems.append(f"{lang}: {key} usa marcadores diferentes.")
    return problems
