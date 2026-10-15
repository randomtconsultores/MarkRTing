from datetime import timedelta

from test_subscription import T0, make

WINDOW = timedelta(hours=48)


def queue(repo, email="a@a.pt", at=T0, purposes=("newsletter",)):
    return repo.add_limited(email, "Ana", "Silva", "pt", purposes, at, at - timedelta(hours=24), 3)


def due(repo, limit=10, now=T0):
    return [r.id for r in repo.list_due(now, now - WINDOW, limit)]


def test_list_due_returns_only_pending_unconfirmed_requests_inside_the_window(db):
    _, repo, _ = make(db)
    expired = queue(repo, "old@a.pt", T0 - WINDOW - timedelta(seconds=1))
    first = queue(repo, "b@a.pt", T0 - timedelta(hours=1))
    second = queue(repo, "c@a.pt", T0)
    postponed = queue(repo, "d@a.pt")
    repo.reschedule(postponed, T0 + timedelta(minutes=5), "451")
    confirmed = queue(repo, "e@a.pt")
    repo.mark_confirmed(confirmed, T0)
    skipped = queue(repo, "f@a.pt")
    repo.mark_skipped(skipped, "suprimido")
    assert expired not in due(repo)
    assert due(repo) == [first, second]
    assert due(repo, limit=1) == [first]
    assert repo.list_due(T0, T0 - WINDOW, 10)[0].purposes == ("newsletter",)


def test_a_claim_is_exclusive_and_counts_the_attempt(db):
    _, repo, _ = make(db)
    request_id = queue(repo)
    assert repo.claim(request_id, T0) is True
    assert repo.claim(request_id, T0) is False
    claimed = repo.get(request_id)
    assert claimed.delivery_status == "sending" and claimed.attempts == 1
    assert due(repo) == []


def test_a_confirmed_request_cannot_be_claimed(db):
    _, repo, _ = make(db)
    request_id = queue(repo)
    repo.mark_confirmed(request_id, T0)
    assert repo.claim(request_id, T0) is False


def test_stale_claims_return_to_pending_and_release_can_undo_the_attempt(db):
    _, repo, _ = make(db)
    request_id = queue(repo)
    repo.claim(request_id, T0)
    assert repo.release_stale(T0) == 0
    assert repo.release_stale(T0 + timedelta(minutes=30)) == 1
    again = repo.get(request_id)
    assert again.delivery_status == "pending" and again.attempts == 1
    repo.claim(request_id, T0)
    repo.release(request_id, undo_attempt=True)
    assert repo.get(request_id).attempts == 1
    repo.claim(request_id, T0)
    repo.release(request_id, undo_attempt=False)
    kept = repo.get(request_id)
    assert kept.delivery_status == "pending" and kept.attempts == 2


def test_state_transitions_and_error_truncation(db):
    _, repo, _ = make(db)
    sent, failed, skipped, retried = (queue(repo, f"{n}@a.pt") for n in "wxyz")
    repo.mark_sent(sent, T0)
    repo.mark_failed(failed, "x" * 600)
    repo.mark_skipped(skipped, "suprimido")
    repo.reschedule(retried, T0 + timedelta(seconds=60), "451 mais tarde")
    assert (repo.get(sent).delivery_status, repo.get(sent).sent_at) == ("sent", T0)
    assert repo.get(failed).delivery_status == "failed" and len(repo.get(failed).last_error) == 500
    assert (repo.get(skipped).delivery_status, repo.get(skipped).skip_reason) == ("skipped", "suprimido")
    again = repo.get(retried)
    assert again.delivery_status == "pending" and again.last_error == "451 mais tarde"
    assert again.next_attempt_at == T0 + timedelta(seconds=60)


def test_confirmation_is_recorded_once(db):
    _, repo, _ = make(db)
    request_id = queue(repo)
    assert repo.mark_confirmed(request_id, T0) is True
    assert repo.mark_confirmed(request_id, T0 + timedelta(hours=1)) is False
    assert repo.get(request_id).confirmed_at == T0


def test_count_sent_since_counts_only_sent_requests_from_that_instant(db):
    _, repo, _ = make(db)
    first, second = queue(repo, "a@a.pt"), queue(repo, "b@a.pt")
    repo.mark_sent(first, T0)
    repo.mark_sent(second, T0 - timedelta(days=1))
    queue(repo, "c@a.pt")
    assert repo.count_sent_since(T0 - timedelta(hours=1)) == 1
    assert repo.count_sent_since(T0 - timedelta(days=2)) == 2
