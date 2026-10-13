import pytest

from mini_marketo.domain.contact import ValidationError
from mini_marketo.domain.segment import Criterion
from mini_marketo.repositories.segment_json import criteria_from_json, criteria_to_json


def test_json_round_trip():
    original = (
        Criterion("first_name", "contains", "an"),
        Criterion("company", "is_empty"),
        Criterion("created_at", "after", "2026-01-31"),
    )
    assert criteria_from_json(criteria_to_json(original)) == original


def test_non_ascii_values_are_stored_readable():
    text = criteria_to_json((Criterion("first_name", "is", "João"),))
    assert "João" in text
    assert criteria_from_json(text)[0].value == "João"


@pytest.mark.parametrize("bad", ["not json", "{}", "[1]"])
def test_malformed_stored_criteria_are_rejected(bad):
    with pytest.raises(ValidationError):
        criteria_from_json(bad)
