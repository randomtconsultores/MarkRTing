"""Formato de armazenamento dos critérios: texto JSON, igual nos dois backends."""

import json
from collections.abc import Iterable

from ..domain.contact import ValidationError
from ..domain.segment import Criterion, criteria_from_data, criteria_to_data


def criteria_to_json(criteria: Iterable[Criterion]) -> str:
    return json.dumps(criteria_to_data(criteria), ensure_ascii=False, separators=(",", ":"))


def criteria_from_json(text: str) -> tuple[Criterion, ...]:
    try:
        data = json.loads(text)
    except (TypeError, ValueError) as exc:
        raise ValidationError("Critérios guardados mal formados.") from exc
    return criteria_from_data(data)
