from datetime import datetime, timedelta

import pytest
from campaign_support import T0, Setup

from mini_marketo.domain.contact import ValidationError


def insert_message(db, campaign_id, contact_id, email="x@a.pt", status="pending", attempts=0):
    p = db.p
    db.execute(
        "INSERT INTO email_messages (campaign_id, contact_id, email, status, attempts) "
        f"VALUES ({p}, {p}, {p}, {p}, {p})",
        (campaign_id, contact_id, email, status, attempts),
    )


def test_create_campaign_normalizes_and_snapshots_the_segment(db):
    s = Setup(db)
    c = s.campaign(name="  Outubro  ", subject="Olá\r\nBcc: x@y.z")
    assert c.id is not None and c.name == "Outubro"
    assert c.subject == "Olá Bcc: x@y.z"
    assert c.status == "draft" and c.queued_at is None
    assert c.segment_id == s.segment.id and c.segment_name == "Todos"
    assert c.created_at.tzinfo is None
    assert s.service.get_campaign(c.id) == c


def test_campaign_name_is_unique_ignoring_case(db):
    s = Setup(db)
    s.campaign(name="Outubro")
    with pytest.raises(ValidationError):
        s.campaign(name="outubro")
    assert len(s.service.list_campaigns()) == 1


def test_unknown_segment_and_invalid_data_store_nothing(db):
    s = Setup(db)
    with pytest.raises(ValidationError):
        s.campaign(segment_id=9999)
    with pytest.raises(ValidationError):
        s.campaign(purpose="outra")
    with pytest.raises(ValidationError):
        s.campaign(subject="   ")
    assert s.service.list_campaigns() == []


def test_unknown_campaign_is_reported(db):
    s = Setup(db)
    with pytest.raises(ValidationError):
        s.service.get_campaign(999)
    with pytest.raises(ValidationError):
        s.service.enqueue(999)


def test_enqueue_creates_pending_for_eligible_and_skipped_for_the_rest(db):
    s = Setup(db)
    c = s.campaign()
    assert s.service.enqueue(c.id) == {"pending": 2, "skipped": 1}
    assert s.message_for(c.id, s.ana).status == "pending"
    assert s.message_for(c.id, s.ana).email == "ana@a.pt"
    eva = s.message_for(c.id, s.eva)
    assert eva.status == "skipped" and eva.skip_reason == "sem_consentimento"
    queued = s.service.get_campaign(c.id)
    assert queued.status == "queued" and queued.queued_at == T0
    assert [m.contact_id for m in s.repo.problem_messages(c.id)] == [s.eva.id]


def test_withdrawn_consent_is_skipped_at_enqueue(db):
    s = Setup(db)
    s.consents.record(s.rui.id, "newsletter", "withdrawn", "admin")
    c = s.campaign()
    assert s.service.enqueue(c.id) == {"pending": 1, "skipped": 2}
    assert s.message_for(c.id, s.rui).status == "skipped"


def test_consent_of_another_purpose_does_not_count(db):
    s = Setup(db)
    c = s.campaign(name="Promo", purpose="promotional")
    assert s.service.enqueue(c.id) == {"skipped": 3}


def test_second_enqueue_is_rejected_and_changes_nothing(db):
    s = Setup(db)
    c = s.campaign()
    s.service.enqueue(c.id)
    with pytest.raises(ValidationError, match="já foi enfileirada"):
        s.service.enqueue(c.id)
    assert s.repo.counts(c.id) == {"pending": 2, "skipped": 1}


def test_repository_refuses_to_queue_a_campaign_that_is_not_a_draft(db):
    s = Setup(db)
    c = s.campaign()
    s.service.enqueue(c.id)
    with pytest.raises(ValidationError):
        s.repo.queue_campaign(c.id, [(s.ana.id, "ana@a.pt")], [], T0)
    assert s.repo.counts(c.id) == {"pending": 2, "skipped": 1}


def test_deleting_the_segment_keeps_the_campaign_but_blocks_enqueue(db):
    s = Setup(db)
    c = s.campaign()
    s.segments.delete_segment(s.segment.id)
    kept = s.service.get_campaign(c.id)
    assert kept.segment_id is None and kept.segment_name == "Todos"
    with pytest.raises(ValidationError, match="já não existe"):
        s.service.enqueue(c.id)


def test_summary_has_counts_and_problems(db):
    s = Setup(db)
    c = s.campaign()
    s.service.enqueue(c.id)
    summary = s.service.summary(c.id)
    assert summary["campaign"].id == c.id
    assert summary["counts"] == {"pending": 2, "skipped": 1}
    assert [m.skip_reason for m in summary["problems"]] == ["sem_consentimento"]


def test_claim_is_exclusive_counts_the_attempt_and_respects_the_limit(db):
    s = Setup(db)
    c = s.campaign()
    s.service.enqueue(c.id)
    one = s.repo.claim_due(T0, 1)
    assert len(one) == 1 and one[0].status == "sending" and one[0].attempts == 1
    rest = s.repo.claim_due(T0, 10)
    assert len(rest) == 1 and rest[0].id != one[0].id
    assert s.repo.claim_due(T0, 10) == []


def test_release_returns_messages_and_can_undo_the_attempt(db):
    s = Setup(db)
    c = s.campaign()
    s.service.enqueue(c.id)
    claimed = s.repo.claim_due(T0, 10)
    s.repo.release([m.id for m in claimed], undo_attempt=True)
    again = s.repo.claim_due(T0, 10)
    assert len(again) == 2 and all(m.attempts == 1 for m in again)
    s.repo.release([m.id for m in again], undo_attempt=False)
    assert all(s.repo.get_message(m.id).attempts == 1 for m in again)
    assert all(s.repo.get_message(m.id).status == "pending" for m in again)
    s.repo.release([], undo_attempt=True)


def test_stale_claims_return_to_pending(db):
    s = Setup(db)
    c = s.campaign()
    s.service.enqueue(c.id)
    claimed = s.repo.claim_due(T0, 10)
    assert s.repo.release_stale(T0) == 0
    assert s.repo.release_stale(T0 + timedelta(minutes=30)) == 2
    assert all(s.repo.get_message(m.id).status == "pending" for m in claimed)


def test_state_transitions_and_error_truncation(db):
    s = Setup(db)
    c = s.campaign()
    s.service.enqueue(c.id)
    ana = s.message_for(c.id, s.ana)
    rui = s.message_for(c.id, s.rui)
    s.repo.mark_sent(ana.id, T0)
    sent = s.repo.get_message(ana.id)
    assert sent.status == "sent" and sent.sent_at == T0
    s.repo.mark_failed(rui.id, "x" * 600)
    failed = s.repo.get_message(rui.id)
    assert failed.status == "failed" and len(failed.last_error) == 500
    s.repo.mark_skipped(rui.id, "email_removido")
    assert s.repo.get_message(rui.id).skip_reason == "email_removido"


def test_reschedule_hides_the_message_until_it_is_due(db):
    s = Setup(db)
    c = s.campaign()
    s.service.enqueue(c.id)
    ana = s.message_for(c.id, s.ana)
    s.repo.reschedule(ana.id, T0 + timedelta(seconds=60), "451 tente mais tarde")
    ids_now = [m.id for m in s.repo.claim_due(T0, 10)]
    assert ana.id not in ids_now
    later = s.repo.claim_due(T0 + timedelta(seconds=61), 10)
    assert [m.id for m in later] == [ana.id]
    assert later[0].last_error == "451 tente mais tarde"


def test_count_sent_since_counts_only_sent_messages_from_that_instant(db):
    s = Setup(db)
    c = s.campaign()
    s.service.enqueue(c.id)
    for message in s.repo.claim_due(T0, 10):
        s.repo.mark_sent(message.id, T0)
    assert s.repo.count_sent_since(datetime(2026, 1, 15)) == 2
    assert s.repo.count_sent_since(datetime(2026, 1, 16)) == 0


def test_database_rejects_a_second_message_for_the_same_contact_and_campaign(db):
    s = Setup(db)
    c = s.campaign()
    insert_message(db, c.id, s.ana.id)
    with pytest.raises(db.integrity_error):
        insert_message(db, c.id, s.ana.id)


@pytest.mark.parametrize(
    "kwargs",
    [{"status": "enviada"}, {"attempts": -1}, {"email": "A@B.PT"}, {"email": ""}],
)
def test_database_rejects_invalid_message_values(db, kwargs):
    s = Setup(db)
    c = s.campaign()
    with pytest.raises(db.integrity_error):
        insert_message(db, c.id, s.ana.id, **kwargs)


def test_database_rejects_invalid_campaign_values(db):
    s = Setup(db)
    p = db.p
    with pytest.raises(db.integrity_error):
        db.execute(
            "INSERT INTO campaigns (name, subject, body_text, purpose, segment_name) "
            f"VALUES ({p}, {p}, {p}, {p}, {p})",
            ("A", "B", "C", "inventada", "Todos"),
        )
    with pytest.raises(db.integrity_error):
        db.execute(
            "INSERT INTO campaigns (name, subject, body_text, purpose, segment_name) "
            f"VALUES ({p}, {p}, {p}, {p}, {p})",
            ("   ", "B", "C", "newsletter", "Todos"),
        )
    assert s.service.list_campaigns() == []


def test_a_contact_with_messages_cannot_be_deleted(db):
    s = Setup(db)
    c = s.campaign()
    s.service.enqueue(c.id)
    with pytest.raises(db.integrity_error):
        db.execute(f"DELETE FROM contacts WHERE id = {db.p}", (s.ana.id,))


def test_migration_v5_applies_on_top_of_v4_and_keeps_existing_data(db):
    s = Setup(db)
    db.execute("DROP TABLE email_messages")
    db.execute("DROP TABLE campaigns")
    db.set_version(4)
    again = db.reopen()
    try:
        assert again.version() == again.module.SCHEMA_VERSION
        assert len(again.service().list_contacts()) == 3
        assert again.scalar("SELECT count(*) FROM campaigns") == 0
        assert again.scalar("SELECT count(*) FROM email_messages") == 0
        assert len(again.segment_service().list_segments()) == 1
    finally:
        again.close()
    assert s.ana.id is not None
