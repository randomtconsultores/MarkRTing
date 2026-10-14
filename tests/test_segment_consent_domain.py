from datetime import datetime

import pytest

from mini_marketo.domain.consent import ConsentRecord
from mini_marketo.domain.contact import Contact, ValidationError
from mini_marketo.domain.segment import Criterion, Segment

NOW = datetime(2026, 10, 14, 12, 0)


def record(id, contact_id, purpose, decision):
    return ConsentRecord(id, contact_id, purpose, decision, "admin", "a@a.pt", NOW)


def contact(id, company=None):
    return Contact(id, "Ana", "Silva", ("a@a.pt",), company_name=company)


def ids(members):
    return [m.id for m in members]


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("granted", "granted"),
        ("concedido", "granted"),
        ("  Concedido ", "granted"),
        ("RETIRADO", "withdrawn"),
        ("withdrawn", "withdrawn"),
        ("sem registo", "none"),
        ("Sem   Registo", "none"),
        ("none", "none"),
    ],
)
def test_value_accepts_labels_and_internal_keys(raw, expected):
    assert Criterion("consent_newsletter", "is", raw).value == expected


def test_display_value_is_the_portuguese_label_only_for_consent():
    assert Criterion("consent_newsletter", "is", "granted").display_value == "Concedido"
    assert Criterion("consent_promotional", "is_not", "none").display_value == "Sem registo"
    assert Criterion("company", "is", "ACME").display_value == "ACME"
    assert Criterion("company", "is_empty").display_value is None


@pytest.mark.parametrize(
    "field, operator, value, message",
    [
        ("consent_newsletter", "is", "talvez", "Estado de consentimento inválido"),
        ("consent_newsletter", "is", "", "precisa de um valor"),
        ("consent_newsletter", "contains", "concedido", "não se aplica"),
        ("consent_newsletter", "is_empty", None, "não se aplica"),
        ("consent_outra", "is", "concedido", "desconhecido"),
    ],
)
def test_invalid_consent_criteria_are_rejected(field, operator, value, message):
    with pytest.raises(ValidationError, match=message):
        Criterion(field, operator, value)


def test_is_matches_the_current_status():
    records = [record(1, 1, "newsletter", "granted")]
    c = contact(1)
    assert Criterion("consent_newsletter", "is", "granted").matches(c, records)
    assert not Criterion("consent_newsletter", "is", "withdrawn").matches(c, records)
    assert not Criterion("consent_newsletter", "is", "none").matches(c, records)


def test_is_not_is_the_negation():
    c = contact(1)
    granted = [record(1, 1, "newsletter", "granted")]
    assert not Criterion("consent_newsletter", "is_not", "granted").matches(c, granted)
    assert Criterion("consent_newsletter", "is_not", "granted").matches(c, [])


def test_without_records_the_status_is_none():
    c = contact(1)
    assert Criterion("consent_newsletter", "is", "none").matches(c)
    assert not Criterion("consent_newsletter", "is", "granted").matches(c)


def test_later_withdrawal_prevails_regardless_of_list_order():
    c = contact(1)
    history = [record(1, 1, "newsletter", "granted"), record(2, 1, "newsletter", "withdrawn")]
    for records in (history, list(reversed(history))):
        assert Criterion("consent_newsletter", "is", "withdrawn").matches(c, records)
        assert not Criterion("consent_newsletter", "is", "granted").matches(c, records)


def test_purposes_are_independent():
    c = contact(1)
    records = [record(1, 1, "newsletter", "granted")]
    assert Criterion("consent_newsletter", "is", "granted").matches(c, records)
    assert Criterion("consent_promotional", "is", "none").matches(c, records)
    assert not Criterion("consent_promotional", "is", "granted").matches(c, records)


def test_consent_criterion_survives_a_data_round_trip_with_the_internal_key():
    criterion = Criterion("consent_newsletter", "is", "concedido")
    data = criterion.to_data()
    assert data == {"field": "consent_newsletter", "operator": "is", "value": "granted"}
    assert Criterion.from_data(data) == criterion


def test_segment_members_use_each_contacts_own_records():
    contacts = [contact(1), contact(2), contact(3)]
    by_contact = {
        1: [record(1, 1, "newsletter", "granted")],
        2: [record(2, 2, "newsletter", "granted"), record(3, 2, "newsletter", "withdrawn")],
    }

    def members(value):
        segment = Segment(
            None, "S", "", "all", (Criterion("consent_newsletter", "is", value),)
        )
        return ids(segment.members(contacts, by_contact))

    assert members("granted") == [1]
    assert members("withdrawn") == [2]
    assert members("none") == [3]


def test_consent_combines_with_other_criteria():
    contacts = [contact(1, company="ACME"), contact(2)]
    by_contact = {
        1: [record(1, 1, "newsletter", "granted")],
        2: [record(2, 2, "newsletter", "granted")],
    }
    segment = Segment(
        None,
        "S",
        "",
        "all",
        (
            Criterion("consent_newsletter", "is", "granted"),
            Criterion("company", "is_not_empty"),
        ),
    )
    assert ids(segment.members(contacts, by_contact)) == [1]


def test_uses_consent_only_when_a_consent_criterion_exists():
    with_consent = Segment(None, "S", "", "all", (Criterion("consent_newsletter", "is", "none"),))
    without = Segment(None, "S", "", "all", (Criterion("company", "is_empty"),))
    assert with_consent.uses_consent is True
    assert without.uses_consent is False
