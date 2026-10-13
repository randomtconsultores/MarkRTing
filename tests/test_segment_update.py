import pytest

from mini_marketo.domain.contact import ValidationError
from mini_marketo.domain.segment import Criterion

ACME = [Criterion("company", "is", "acme")]
OTHER = [Criterion("first_name", "starts_with", "r")]


def test_update_changes_everything_but_id_and_created_at(db):
    segments = db.segment_service()
    original = segments.create_segment("VIP", "antes", "all", ACME)
    updated = segments.update_segment(original.id, " Novo ", " depois ", "any", OTHER + ACME)
    assert updated.id == original.id and updated.created_at == original.created_at
    assert (updated.name, updated.description, updated.match_mode) == ("Novo", "depois", "any")
    assert updated.criteria == tuple(OTHER + ACME)
    assert segments.get_segment(original.id) == updated


def test_a_segment_can_change_only_the_case_of_its_own_name(db):
    segments = db.segment_service()
    original = segments.create_segment("vip", "", "all", ACME)
    assert segments.update_segment(original.id, "VIP", "", "all", ACME).name == "VIP"


def test_update_to_another_segments_name_is_rejected(db):
    segments = db.segment_service()
    segments.create_segment("A", "", "all", ACME)
    second = segments.create_segment("B", "", "all", ACME)
    with pytest.raises(ValidationError):
        segments.update_segment(second.id, "a", "", "all", ACME)
    assert segments.get_segment(second.id).name == "B"


def test_invalid_update_changes_nothing(db):
    segments = db.segment_service()
    original = segments.create_segment("VIP", "d", "all", ACME)
    with pytest.raises(ValidationError):
        segments.update_segment(original.id, "VIP", "d", "all", [])
    with pytest.raises(ValidationError):
        segments.update_segment(original.id, "Outro", "d", "some", ACME)
    assert segments.get_segment(original.id) == original


def test_updating_an_unknown_segment_is_reported(db):
    segments = db.segment_service()
    with pytest.raises(ValidationError):
        segments.update_segment(999, "X", "", "all", ACME)


def test_members_follow_the_new_criteria(db):
    contacts, segments = db.service(), db.segment_service()
    acme = contacts.create_company("ACME")
    contacts.create_contact("Ana", "Silva", "ana@a.pt", acme.id)
    contacts.create_contact("Rui", "Costa", "rui@a.pt")
    segment = segments.create_segment("S", "", "all", ACME)
    assert [c.first_name for c in segments.members(segment.id)] == ["Ana"]
    segments.update_segment(segment.id, "S", "", "all", OTHER)
    assert [c.first_name for c in segments.members(segment.id)] == ["Rui"]
