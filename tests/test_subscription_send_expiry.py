from datetime import timedelta

from test_delivery import FakeMailer
from test_subscription import T0
from test_subscription_delivery import queue
from test_subscription_sender import build

TTL = timedelta(hours=48)


def test_a_request_expiring_during_the_batch_is_not_sent(db):
    mailer = FakeMailer()
    sender, repo, _, _ = build(db, mailer)
    created = T0 - TTL + timedelta(seconds=1)
    first = queue(repo, "first@a.pt", at=created)
    second = queue(repo, "second@a.pt", at=created)
    clock = [T0]
    sender.now = lambda: clock[0]
    def advance(seconds):
        clock[0] += timedelta(seconds=seconds)
    sender.sleep = advance
    result = sender.run_once()
    assert (result.sent, result.skipped, result.failed) == (1, 1, 0)
    assert [message[0] for message in mailer.sent] == ["first@a.pt"]
    assert repo.get(first).delivery_status == "sent"
    skipped = repo.get(second)
    assert (skipped.delivery_status, skipped.skip_reason) == ("skipped", "expirado")
    assert sender.run_once().sent == 0


def test_exact_expiration_at_send_time_skips_without_calling_smtp(db):
    mailer = FakeMailer()
    sender, repo, _, _ = build(db, mailer)
    request_id = queue(repo, at=T0 - TTL)
    result = sender.run_once()
    assert (result.sent, result.skipped) == (0, 1)
    assert mailer.sent == []
    assert repo.get(request_id).skip_reason == "expirado"


def test_expiration_while_preparing_the_message_is_respected(db):
    mailer = FakeMailer()
    sender, repo, _, _ = build(db, mailer)
    created = T0 - TTL + timedelta(seconds=1)
    request_id = queue(repo, at=created)
    clock = [T0]
    sender.now = lambda: clock[0]
    original_links = sender.links
    class SlowLinks:
        def url_for(self, value):
            clock[0] += timedelta(seconds=2)
            return original_links.url_for(value)
    sender.links = SlowLinks()
    result = sender.run_once()
    assert (result.sent, result.skipped) == (0, 1)
    assert mailer.sent == []
    assert repo.get(request_id).skip_reason == "expirado"
