import pytest

from mini_marketo.domain.segment import Criterion


def setup(db):
    contacts, segments, consents = db.service(), db.segment_service(), db.consent_service()
    ana = contacts.create_contact("Ana", "Silva", "ana@a.pt")
    rui = contacts.create_contact("Rui", "Costa", "rui@a.pt")
    eva = contacts.create_contact("Eva", "Lopes", "eva@a.pt")
    consents.record(ana.id, "newsletter", "granted", "admin")
    consents.record(rui.id, "newsletter", "granted", "admin")
    consents.record(rui.id, "newsletter", "withdrawn", "admin")
    return segments, consents, ana, rui, eva


def create(segments, operator, value, field="consent_newsletter"):
    # O nome do segmento é único: deriva-se do critério para permitir vários por teste.
    name = f"{field} {operator} {value}"
    return segments.create_segment(name, "", "all", [Criterion(field, operator, value)])


def names(members):
    return sorted(m.first_name for m in members)


def test_members_by_each_consent_status(db):
    segments, *_ = setup(db)
    assert names(segments.members(create(segments, "is", "granted").id)) == ["Ana"]
    assert names(segments.members(create(segments, "is", "withdrawn").id)) == ["Rui"]
    assert names(segments.members(create(segments, "is", "none").id)) == ["Eva"]
    assert names(segments.members(create(segments, "is_not", "granted").id)) == ["Eva", "Rui"]


def test_a_withdrawal_removes_the_contact_from_the_segment(db):
    segments, consents, ana, *_ = setup(db)
    segment = create(segments, "is", "granted")
    assert names(segments.members(segment.id)) == ["Ana"]
    consents.record(ana.id, "newsletter", "withdrawn", "admin")
    assert segments.members(segment.id) == []


def test_purposes_are_independent_in_segments(db):
    segments, *_ = setup(db)
    segment = create(segments, "is", "none", field="consent_promotional")
    assert names(segments.members(segment.id)) == ["Ana", "Eva", "Rui"]


def test_list_with_counts_evaluates_consent_segments(db):
    segments, *_ = setup(db)
    create(segments, "is", "granted")
    create(segments, "is", "none")
    assert [count for _, count in segments.list_with_counts()] == [1, 1]


def test_membership_is_not_authorization(db):
    segments, consents, _, _, eva = setup(db)
    segment = create(segments, "is", "none")
    assert names(segments.members(segment.id)) == ["Eva"]
    assert consents.is_eligible(eva.id, "newsletter") is False


def test_criterion_is_stored_with_the_internal_key_and_reloaded(db):
    segments, *_ = setup(db)
    created = create(segments, "is", "concedido")
    reloaded = segments.get_segment(created.id)
    assert reloaded.criteria[0].value == "granted"
    assert reloaded.criteria[0].display_value == "Concedido"


def test_list_all_returns_the_whole_history_in_order(db):
    segments, *_ = setup(db)
    records = segments.consents.list_all()
    assert [r.decision for r in records] == ["granted", "granted", "withdrawn"]
    assert [r.id for r in records] == sorted(r.id for r in records)


def test_consent_segments_fail_loudly_without_the_consent_repository(db):
    segments, *_ = setup(db)
    segment = create(segments, "is", "granted")
    segments.consents = None
    with pytest.raises(RuntimeError, match="consentimento"):
        segments.members(segment.id)
    with pytest.raises(RuntimeError, match="consentimento"):
        segments.list_with_counts()


def test_other_segments_still_work_without_the_consent_repository(db):
    segments, *_ = setup(db)
    segment = segments.create_segment("S", "", "all", [Criterion("first_name", "is", "Ana")])
    segments.consents = None
    assert names(segments.members(segment.id)) == ["Ana"]
    assert [count for _, count in segments.list_with_counts()] == [1]
