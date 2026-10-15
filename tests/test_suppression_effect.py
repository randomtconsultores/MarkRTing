import pytest
from campaign_support import Setup
from test_delivery import LINKS, FakeMailer, make_worker, queued

from mini_marketo.delivery import DeliveryLimits, DeliveryWorker
from mini_marketo.mailer import SendError
from mini_marketo.services.campaign_service import CampaignService


def test_the_worker_and_the_service_need_the_suppression_list():
    with pytest.raises(ValueError, match="supressão"):
        DeliveryWorker(None, None, None, None, DeliveryLimits(), LINKS, None)
    with pytest.raises(ValueError, match="supressão"):
        CampaignService(None, None, None, None)


def test_enqueue_skips_a_suppressed_address_even_with_consent(db):
    s = Setup(db)
    s.suppressions.add("ana@a.pt", "hard_bounce", "event", "")
    c = queued(s)
    ana = s.message_for(c.id, s.ana)
    assert ana.status == "skipped" and ana.skip_reason == "suprimido"
    assert s.repo.counts(c.id) == {"pending": 1, "skipped": 2}


def test_a_suppression_blocks_every_purpose(db):
    s = Setup(db)
    s.consents.record(s.ana.id, "promotional", "granted", "admin")
    s.suppressions.add("ana@a.pt", "manual", "admin", "")
    c = queued(s, purpose="promotional")
    ana = s.message_for(c.id, s.ana)
    assert ana.status == "skipped" and ana.skip_reason == "suprimido"


def test_a_suppression_after_enqueue_is_respected_at_send_time(db):
    s = Setup(db)
    c = queued(s)
    s.suppressions.add("rui@a.pt", "complaint", "event", "")
    mailer = FakeMailer()
    summary = make_worker(s, mailer)[0].run_once()
    assert (summary.sent, summary.skipped) == (1, 1)
    rui = s.message_for(c.id, s.rui)
    assert rui.status == "skipped" and rui.skip_reason == "suprimido"
    assert [m[0] for m in mailer.sent] == ["ana@a.pt"]


def test_a_removed_manual_suppression_lets_the_address_receive_again(db):
    s = Setup(db)
    s.suppressions.add("ana@a.pt", "manual", "admin", "")
    s.suppressions.remove("ana@a.pt")
    c = queued(s)
    assert s.message_for(c.id, s.ana).status == "pending"


def test_a_permanent_address_rejection_suppresses_the_address(db):
    s = Setup(db)
    c = queued(s)
    error = SendError("permanent", "550 5.1.1 user unknown", address_rejected=True)
    mailer = FakeMailer(fail={"ana@a.pt": [error]})
    summary = make_worker(s, mailer)[0].run_once()
    assert (summary.sent, summary.failed, summary.suppressed) == (1, 1, 1)
    assert s.message_for(c.id, s.ana).status == "failed"
    record = next(x for x in s.suppressions.list() if x.email == "ana@a.pt")
    assert (record.reason, record.source) == ("hard_bounce", "smtp")
    assert "5.1.1" in record.detail
    later = s.campaign(name="Novembro")
    s.service.enqueue(later.id)
    ana_later = s.message_for(later.id, s.ana)
    assert ana_later.status == "skipped" and ana_later.skip_reason == "suprimido"


def test_a_policy_rejection_does_not_suppress_the_address(db):
    s = Setup(db)
    queued(s)
    error = SendError("permanent", "550 5.7.1 blocked by policy")
    mailer = FakeMailer(fail={"ana@a.pt": [error]})
    summary = make_worker(s, mailer)[0].run_once()
    assert (summary.failed, summary.suppressed) == (1, 0)
    assert not s.suppressions.is_suppressed("ana@a.pt")


def test_a_temporary_failure_does_not_suppress_the_address(db):
    s = Setup(db)
    queued(s)
    mailer = FakeMailer(fail={"ana@a.pt": [SendError("temporary", "451 4.1.1 later")]})
    summary = make_worker(s, mailer)[0].run_once()
    assert (summary.retried, summary.suppressed) == (1, 0)
    assert not s.suppressions.is_suppressed("ana@a.pt")
