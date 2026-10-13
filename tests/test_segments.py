from datetime import datetime, timedelta, timezone

import pytest

from mini_marketo.domain.contact import ValidationError
from mini_marketo.domain.segment import Criterion

ACME = [Criterion("company", "is", "acme")]


def test_create_and_get_round_trip(db):
    segments = db.segment_service()
    created = segments.create_segment(
        "  Clientes VIP ",
        " os melhores ",
        "any",
        [Criterion("first_name", "starts_with", "a"), Criterion("company", "is_empty")],
    )
    assert created.id is not None
    assert created.name == "Clientes VIP" and created.description == "os melhores"
    assert created.match_mode == "any"
    assert created.criteria == (
        Criterion("first_name", "starts_with", "a"),
        Criterion("company", "is_empty"),
    )
    assert created.created_at.tzinfo is None
    now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
    assert abs(now_utc - created.created_at) < timedelta(seconds=30)
    assert segments.get_segment(created.id) == created


def test_every_field_and_operator_survives_storage(db):
    criteria = [
        Criterion("first_name", "is", "Ana"),
        Criterion("last_name", "contains", "il"),
        Criterion("email", "starts_with", "a@"),
        Criterion("email", "is_not", "x@y.z"),
        Criterion("company", "is_not_empty"),
        Criterion("created_at", "before", "2030-01-01"),
    ]
    segments = db.segment_service()
    created = segments.create_segment("Todos", "", "all", criteria)
    assert segments.get_segment(created.id).criteria == tuple(criteria)


def test_duplicate_name_ignoring_case_is_rejected(db):
    segments = db.segment_service()
    segments.create_segment("VIP", "", "all", ACME)
    with pytest.raises(ValidationError):
        segments.create_segment("vip", "", "any", ACME)
    assert len(segments.list_segments()) == 1


def test_invalid_segment_is_not_stored(db):
    segments = db.segment_service()
    with pytest.raises(ValidationError):
        segments.create_segment("Sem criterios", "", "all", [])
    with pytest.raises(ValidationError):
        segments.create_segment("Modo errado", "", "some", ACME)
    assert segments.list_segments() == []


def test_list_is_ordered_by_name_ignoring_case(db):
    segments = db.segment_service()
    for name in ("banana", "Apple", "cherry"):
        segments.create_segment(name, "", "all", ACME)
    assert [s.name for s in segments.list_segments()] == ["Apple", "banana", "cherry"]


def test_unknown_segment_is_reported(db):
    segments = db.segment_service()
    with pytest.raises(ValidationError):
        segments.get_segment(999)
    with pytest.raises(ValidationError):
        segments.delete_segment(999)
    with pytest.raises(ValidationError):
        segments.members(999)


def test_delete_removes_the_segment(db):
    segments = db.segment_service()
    created = segments.create_segment("VIP", "", "all", ACME)
    segments.delete_segment(created.id)
    assert segments.list_segments() == []
    with pytest.raises(ValidationError):
        segments.get_segment(created.id)


def test_members_are_computed_from_the_current_contacts(db):
    contacts, segments = db.service(), db.segment_service()
    acme = contacts.create_company("ACME")
    contacts.create_contact("Ana", "Silva", "ana@a.pt", acme.id)
    contacts.create_contact("Rui", "Costa", "rui@a.pt")
    segment = segments.create_segment("ACME", "", "all", ACME)
    assert [c.first_name for c in segments.members(segment.id)] == ["Ana"]
    contacts.create_contact("Eva", "Lima", "eva@a.pt", acme.id)
    assert sorted(c.first_name for c in segments.members(segment.id)) == ["Ana", "Eva"]


def test_members_follow_secondary_emails(db):
    contacts, segments = db.service(), db.segment_service()
    ana = contacts.create_contact("Ana", "Silva", "ana@a.pt")
    segment = segments.create_segment(
        "Trabalho", "", "all", [Criterion("email", "contains", "@work.com")]
    )
    assert segments.members(segment.id) == []
    contacts.add_email(ana.id, "ana@work.com")
    assert [c.first_name for c in segments.members(segment.id)] == ["Ana"]
    contacts.set_primary_email(ana.id, "ana@work.com")
    assert [c.first_name for c in segments.members(segment.id)] == ["Ana"]


def test_list_with_counts(db):
    contacts, segments = db.service(), db.segment_service()
    for first, email in (("Ana", "ana@a.pt"), ("Andre", "andre@a.pt"), ("Rui", "rui@a.pt")):
        contacts.create_contact(first, "X", email)
    segments.create_segment("Starts A", "", "all", [Criterion("first_name", "starts_with", "a")])
    segments.create_segment("Nobody", "", "all", [Criterion("first_name", "is", "zzz")])
    assert [(s.name, n) for s, n in segments.list_with_counts()] == [
        ("Nobody", 0),
        ("Starts A", 2),
    ]


def test_database_rejects_duplicate_names_ignoring_case(db):
    db.execute("INSERT INTO segments (name, match_mode, criteria) VALUES ('A', 'all', '[]')")
    with pytest.raises(db.integrity_error):
        db.execute("INSERT INTO segments (name, match_mode, criteria) VALUES ('a', 'all', '[]')")


def test_database_rejects_invalid_match_mode(db):
    with pytest.raises(db.integrity_error):
        db.execute("INSERT INTO segments (name, match_mode, criteria) VALUES ('A', 'some', '[]')")


def test_database_rejects_blank_name(db):
    with pytest.raises(db.integrity_error):
        db.execute("INSERT INTO segments (name, match_mode, criteria) VALUES ('   ', 'all', '[]')")
