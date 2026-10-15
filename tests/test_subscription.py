import threading
from datetime import datetime, timedelta

import pytest

from mini_marketo.domain.contact import ValidationError
from mini_marketo.domain.subscription import CONFIRMATION_TTL, is_expired, validate_subscription
from mini_marketo.services.subscription_service import SubscriptionService
from mini_marketo.services.suppression_service import SuppressionService

T0 = datetime(2026, 1, 15, 12)


def make(db, now=T0):
    if db.kind == "sqlite":
        from mini_marketo.repositories.subscription_repository import SubscriptionRepository
        from mini_marketo.repositories.suppression_repository import SuppressionRepository
        repo = SubscriptionRepository(db.conn)
        suppressions = SuppressionService(SuppressionRepository(db.conn))
    else:
        from mini_marketo.repositories.postgres_subscription_repository import PostgresSubscriptionRepository
        from mini_marketo.repositories.postgres_suppression_repository import PostgresSuppressionRepository
        repo = PostgresSubscriptionRepository(db.conn)
        suppressions = SuppressionService(PostgresSuppressionRepository(db.conn))
    return SubscriptionService(repo, suppressions, ("pt", "en"), now=lambda: now), repo, suppressions


def test_normalization_and_expiration():
    assert validate_subscription(" A@A.pt ", " Ana ", "Silva", ["newsletter", "newsletter"], "xx", ("pt",)) == (
        "a@a.pt", "Ana", "Silva", ("newsletter",), "pt",
    )
    assert not is_expired(T0, T0 + CONFIRMATION_TTL - timedelta(microseconds=1))
    assert is_expired(T0, T0 + CONFIRMATION_TTL)


@pytest.mark.parametrize("purposes", [[], None, "newsletter", [1], ["outra"], [""]])
def test_invalid_purposes(purposes):
    with pytest.raises(ValidationError):
        validate_subscription("a@a.pt", "Ana", "Silva", purposes, "pt", ("pt",))


def test_request_creates_no_contact_or_consent(db):
    service, repo, _ = make(db)
    assert service.request(" A@A.pt ", "Ana", "Silva", ["promotional", "newsletter"], "en") == "queued"
    request_id = db.scalar("SELECT max(id) FROM subscription_requests")
    record = repo.get(request_id)
    assert record.email == "a@a.pt" and record.lang == "en"
    assert record.purposes == ("newsletter", "promotional")
    assert record.created_at == T0 and record.next_attempt_at == T0
    assert record.delivery_status == "pending" and record.confirmed_at is None
    assert repo.get(999999) is None
    assert db.scalar("SELECT count(*) FROM contacts") == 0
    assert db.scalar("SELECT count(*) FROM consent_events") == 0


def test_suppressed_address_is_not_queued(db):
    service, _, suppressions = make(db)
    suppressions.add("a@a.pt", "complaint", "event", "")
    assert service.request("a@a.pt", "Ana", "Silva", ["newsletter"]) == "suppressed"
    assert db.scalar("SELECT count(*) FROM subscription_requests") == 0


def test_rate_limit_is_per_address_and_expires(db):
    service, _, _ = make(db)
    def ask(svc, email="a@a.pt"):
        return svc.request(email, "Ana", "Silva", ["newsletter"])
    assert [ask(service) for _ in range(4)] == ["queued"] * 3 + ["rate_limited"]
    assert ask(service, "b@a.pt") == "queued"
    later, _, _ = make(db, T0 + timedelta(hours=25))
    assert ask(later) == "queued"


def test_invalid_purpose_rolls_back_the_whole_insert(db):
    _, repo, _ = make(db)
    with pytest.raises(db.integrity_error):
        repo.add_limited("a@a.pt", "Ana", "Silva", "pt", ("newsletter", "invalid"), T0, T0 - timedelta(days=1), 3)
    assert db.scalar("SELECT count(*) FROM subscription_requests") == 0
    assert db.scalar("SELECT count(*) FROM subscription_request_purposes") == 0


def test_concurrent_requests_do_not_exceed_the_limit(db):
    outcomes, errors = [], []
    barrier = threading.Barrier(8)
    def work():
        handle = None
        try:
            handle = db.reopen()
            service, _, _ = make(handle)
            barrier.wait(timeout=15)
            outcomes.append(service.request("a@a.pt", "Ana", "Silva", ["newsletter"]))
        except Exception as exc:
            errors.append(exc)
        finally:
            if handle is not None:
                handle.close()
    threads = [threading.Thread(target=work) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert not any(thread.is_alive() for thread in threads)
    assert errors == []
    assert outcomes.count("queued") == 3
    assert outcomes.count("rate_limited") == 5
    assert db.scalar("SELECT count(*) FROM subscription_requests") == 3
