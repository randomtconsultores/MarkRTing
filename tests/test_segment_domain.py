from datetime import datetime

import pytest

from mini_marketo.domain.contact import Contact, ValidationError
from mini_marketo.domain.segment import (
    Criterion,
    Segment,
    criteria_from_data,
    criteria_to_data,
)


def contact(
    first="Ana",
    last="Silva",
    emails=("ana@a.pt",),
    company=None,
    created=datetime(2026, 10, 13, 12, 0),
):
    return Contact(
        id=1,
        first_name=first,
        last_name=last,
        emails=tuple(emails),
        company_id=None if company is None else 1,
        company_name=company,
        created_at=created,
    )


ONE = Criterion("first_name", "is", "a")


# --- Validação do critério ---------------------------------------------------


def test_unknown_field_is_rejected():
    with pytest.raises(ValidationError):
        Criterion("phone", "is", "1")


@pytest.mark.parametrize(
    "field,operator,value",
    [
        ("first_name", "before", "2026-01-01"),
        ("created_at", "is", "x"),
        ("first_name", "is_empty", None),
        ("email", "is_empty", None),
    ],
)
def test_operator_must_belong_to_field(field, operator, value):
    with pytest.raises(ValidationError):
        Criterion(field, operator, value)


@pytest.mark.parametrize("value", ["", "   ", None])
def test_value_is_required_for_value_operators(value):
    with pytest.raises(ValidationError):
        Criterion("first_name", "is", value)


def test_empty_operators_reject_a_value():
    with pytest.raises(ValidationError):
        Criterion("company", "is_empty", "x")
    assert Criterion("company", "is_empty", "  ").value is None


def test_value_is_limited_in_length():
    assert Criterion("first_name", "is", "x" * 200).value == "x" * 200
    with pytest.raises(ValidationError):
        Criterion("first_name", "is", "x" * 201)


def test_valid_dates_are_normalized():
    assert Criterion("created_at", "after", " 2026-10-13 ").value == "2026-10-13"


@pytest.mark.parametrize(
    "bad",
    ["13/10/2026", "20261013", "2026-13-40", "2026-10-13T10:00", ""],
)
def test_invalid_dates_are_rejected(bad):
    with pytest.raises(ValidationError):
        Criterion("created_at", "after", bad)


# --- Avaliação do critério ---------------------------------------------------


@pytest.mark.parametrize(
    "operator,value,expected",
    [
        ("is", "ANA", True),
        ("is", "An", False),
        ("contains", "N", True),
        ("starts_with", "an", True),
        ("starts_with", "na", False),
        ("is_not", "ana", False),
        ("is_not", "rui", True),
    ],
)
def test_text_operators_ignore_case(operator, value, expected):
    assert Criterion("first_name", operator, value).matches(contact()) is expected


def test_last_name_criterion_uses_the_last_name():
    assert Criterion("last_name", "is", "silva").matches(contact())
    assert not Criterion("first_name", "is", "silva").matches(contact())


@pytest.mark.parametrize(
    "operator,value,expected",
    [
        ("is", "ana.work@b.com", True),
        ("contains", "b.com", True),
        ("starts_with", "ana.w", True),
        ("is", "x@y.z", False),
    ],
)
def test_email_positive_operators_match_any_email(operator, value, expected):
    c = contact(emails=("ana@a.pt", "ana.work@b.com"))
    assert Criterion("email", operator, value).matches(c) is expected


@pytest.mark.parametrize("value,expected", [("ana.work@b.com", False), ("rui@a.pt", True)])
def test_email_is_not_requires_that_no_email_equals_it(value, expected):
    c = contact(emails=("ana@a.pt", "ana.work@b.com"))
    assert Criterion("email", "is_not", value).matches(c) is expected


@pytest.mark.parametrize(
    "company,operator,value,expected",
    [
        (None, "is_empty", None, True),
        ("ACME", "is_empty", None, False),
        (None, "is_not_empty", None, False),
        ("ACME", "is_not_empty", None, True),
        (None, "is", "acme", False),
        (None, "is_not", "acme", True),
        ("ACME Lda", "contains", "acme", True),
        ("ACME", "starts_with", "ac", True),
    ],
)
def test_company_criteria(company, operator, value, expected):
    assert Criterion("company", operator, value).matches(contact(company=company)) is expected


@pytest.mark.parametrize(
    "operator,value,expected",
    [
        ("before", "2026-10-14", True),
        ("before", "2026-10-13", False),
        ("after", "2026-10-12", True),
        ("after", "2026-10-13", False),
    ],
)
def test_date_criteria_compare_the_utc_day(operator, value, expected):
    assert Criterion("created_at", operator, value).matches(contact()) is expected


@pytest.mark.parametrize("operator", ["before", "after"])
def test_missing_creation_date_never_matches(operator):
    assert not Criterion("created_at", operator, "2026-10-13").matches(contact(created=None))


# --- Segmento -------------------------------------------------------------


@pytest.mark.parametrize("name", ["", "   "])
def test_segment_requires_a_name(name):
    with pytest.raises(ValidationError):
        Segment(None, name, "", "all", (ONE,))


def test_segment_cleans_the_name_and_limits_lengths():
    s = Segment(None, "  Clientes   VIP ", "  desc  ", "all", (ONE,))
    assert s.name == "Clientes VIP" and s.description == "desc"
    with pytest.raises(ValidationError):
        Segment(None, "x" * 101, "", "all", (ONE,))
    with pytest.raises(ValidationError):
        Segment(None, "S", "d" * 501, "all", (ONE,))


def test_match_mode_must_be_all_or_any():
    with pytest.raises(ValidationError):
        Segment(None, "S", "", "some", (ONE,))


@pytest.mark.parametrize("count", [0, 21])
def test_segment_needs_between_one_and_twenty_criteria(count):
    with pytest.raises(ValidationError):
        Segment(None, "S", "", "all", (ONE,) * count)


def test_all_mode_requires_every_criterion():
    s = Segment(
        None, "S", "", "all",
        (Criterion("first_name", "is", "ana"), Criterion("company", "is", "acme")),
    )
    assert s.matches(contact(company="ACME"))
    assert not s.matches(contact(company=None))


def test_any_mode_requires_one_criterion():
    s = Segment(
        None, "S", "", "any",
        (Criterion("first_name", "is", "rui"), Criterion("company", "is", "acme")),
    )
    assert s.matches(contact(company="ACME"))
    assert not s.matches(contact(company=None))


def test_members_filters_and_preserves_order():
    s = Segment(None, "S", "", "all", (Criterion("first_name", "starts_with", "a"),))
    people = [contact(first="Ana"), contact(first="Rui"), contact(first="Andre")]
    assert [c.first_name for c in s.members(people)] == ["Ana", "Andre"]


# --- Serialização ---------------------------------------------------------------


def test_criteria_round_trip_through_data():
    original = (
        Criterion("first_name", "contains", "an"),
        Criterion("company", "is_empty"),
        Criterion("created_at", "after", "2026-01-31"),
    )
    assert criteria_from_data(criteria_to_data(original)) == original


@pytest.mark.parametrize(
    "bad",
    [
        None,
        "x",
        [1],
        [{"operator": "is", "value": "x"}],
        [{"field": "first_name", "operator": "is", "value": "a", "extra": 1}],
        [{"field": "first_name", "operator": "is", "value": 5}],
    ],
)
def test_malformed_data_is_rejected(bad):
    with pytest.raises(ValidationError):
        criteria_from_data(bad)
