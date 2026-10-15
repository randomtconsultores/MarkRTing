import threading
from datetime import timedelta

from test_delivery import LINKS
from test_subscription import T0, make
from test_subscription_delivery import queue

from mini_marketo.services.confirmation_service import ConfirmationService
from mini_marketo.subscription_links import SubscriptionLinks
from mini_marketo.unsubscribe import UnsubscribeLinks

TTL = timedelta(hours=48)
LINKS_OK = SubscriptionLinks(LINKS)


def build(db, now=T0):
    _, repo, suppressions = make(db, now)
    confirm = ConfirmationService(
        repo, suppressions, db.service(), db.consent_service(), LINKS_OK, now=lambda: now
    )
    return confirm, repo, suppressions


def test_find_contact_by_email_uses_any_of_its_emails(db):
    service = db.service()
    contact = service.create_contact("Ana", "Silva", "a@a.pt")
    service.add_email(contact.id, "b@a.pt")
    assert service.find_by_email(" B@A.pt ").id == contact.id
    assert service.find_by_email("a@a.pt").id == contact.id
    assert service.find_by_email("x@a.pt") is None
    assert service.find_by_email("") is None and service.find_by_email(None) is None


def test_confirming_creates_the_contact_and_grants_only_the_chosen_purposes(db):
    confirm, repo, _ = build(db)
    request_id = queue(repo, purposes=("newsletter",))
    view = confirm.confirm(LINKS_OK.token_for(request_id))
    assert view is not None and view.confirmed
    contacts = db.service().list_contacts()
    assert len(contacts) == 1
    contact = contacts[0]
    assert (contact.first_name, contact.last_name, contact.emails) == ("Ana", "Silva", ("a@a.pt",))
    consents = db.consent_service()
    assert consents.status(contact.id, "newsletter") == "granted"
    assert consents.status(contact.id, "promotional") == "none"
    history = consents.history(contact.id)
    assert len(history) == 1 and history[0].source == "form"
    assert repo.get(request_id).confirmed_at == T0


def test_both_purposes_can_be_granted_by_one_confirmation(db):
    confirm, repo, _ = build(db)
    request_id = queue(repo, purposes=("newsletter", "promotional"))
    assert confirm.confirm(LINKS_OK.token_for(request_id)).confirmed
    contact = db.service().list_contacts()[0]
    consents = db.consent_service()
    assert consents.status(contact.id, "newsletter") == "granted"
    assert consents.status(contact.id, "promotional") == "granted"


def test_an_existing_contact_is_reused_and_keeps_its_name(db):
    existing = db.service().create_contact("Rui", "Costa", "rui@a.pt")
    db.service().add_email(existing.id, "a@a.pt")
    confirm, repo, _ = build(db)
    request_id = queue(repo, "a@a.pt")
    assert confirm.confirm(LINKS_OK.token_for(request_id)).confirmed
    contacts = db.service().list_contacts()
    assert len(contacts) == 1
    assert (contacts[0].first_name, contacts[0].last_name) == ("Rui", "Costa")
    assert db.consent_service().status(existing.id, "newsletter") == "granted"


def test_confirming_twice_changes_nothing(db):
    confirm, repo, _ = build(db)
    request_id = queue(repo)
    token = LINKS_OK.token_for(request_id)
    confirm.confirm(token)
    later, _, _ = build(db, T0 + timedelta(hours=1))
    again = later.confirm(token)
    assert again is not None and again.confirmed
    contact = db.service().list_contacts()[0]
    assert len(db.consent_service().history(contact.id)) == 1
    assert repo.get(request_id).confirmed_at == T0


def test_an_already_confirmed_link_still_works_after_it_would_have_expired(db):
    confirm, repo, _ = build(db)
    request_id = queue(repo)
    token = LINKS_OK.token_for(request_id)
    confirm.confirm(token)
    later, _, _ = build(db, T0 + TTL + timedelta(days=5))
    assert later.preview(token).confirmed is True


def test_a_granted_purpose_is_not_granted_again_but_a_withdrawn_one_is(db):
    contact = db.service().create_contact("Ana", "Silva", "a@a.pt")
    consents = db.consent_service()
    consents.record(contact.id, "newsletter", "granted", "admin")
    consents.record(contact.id, "promotional", "granted", "admin")
    consents.record(contact.id, "promotional", "withdrawn", "unsubscribe")
    confirm, repo, _ = build(db)
    request_id = queue(repo, purposes=("newsletter", "promotional"))
    confirm.confirm(LINKS_OK.token_for(request_id))
    assert consents.status(contact.id, "newsletter") == "granted"
    assert consents.status(contact.id, "promotional") == "granted"
    assert len(consents.history(contact.id)) == 4


def test_an_expired_request_cannot_be_confirmed(db):
    _, repo, _ = build(db)
    just_in_time = queue(repo, "a@a.pt")
    too_late = queue(repo, "b@a.pt")
    edge, _, _ = build(db, T0 + TTL - timedelta(microseconds=1))
    assert edge.confirm(LINKS_OK.token_for(just_in_time)).confirmed
    expired, _, _ = build(db, T0 + TTL)
    assert expired.confirm(LINKS_OK.token_for(too_late)) is None
    assert expired.preview(LINKS_OK.token_for(too_late)) is None
    assert [c.emails for c in db.service().list_contacts()] == [("a@a.pt",)]
    assert repo.get(too_late).confirmed_at is None


def test_a_suppressed_address_cannot_be_confirmed(db):
    confirm, repo, suppressions = build(db)
    request_id = queue(repo)
    suppressions.add("a@a.pt", "hard_bounce", "smtp", "")
    assert confirm.confirm(LINKS_OK.token_for(request_id)) is None
    assert db.service().list_contacts() == []
    assert db.scalar("SELECT count(*) FROM consent_events") == 0
    assert repo.get(request_id).confirmed_at is None


def test_invalid_tokens_confirm_nothing(db):
    confirm, repo, _ = build(db)
    request_id = queue(repo)
    other_key = SubscriptionLinks(UnsubscribeLinks("https://marketing.exemplo.pt", "z" * 40))
    for token in (
        None, "", "x", LINKS_OK.token_for(request_id + 100), other_key.token_for(request_id),
        LINKS.token_for(request_id),
    ):
        assert confirm.confirm(token) is None, token
        assert confirm.preview(token) is None, token
    assert db.service().list_contacts() == []
    assert repo.get(request_id).confirmed_at is None


def test_the_preview_writes_nothing(db):
    confirm, repo, _ = build(db)
    request_id = queue(repo)
    token = LINKS_OK.token_for(request_id)
    view = confirm.preview(token)
    assert view is not None and view.confirmed is False
    assert view.request.purposes == ("newsletter",)
    assert db.service().list_contacts() == []
    assert repo.get(request_id).confirmed_at is None
    confirm.confirm(token)
    assert confirm.preview(token).confirmed is True


def test_concurrent_confirmations_create_one_contact(db):
    _, repo, _ = build(db)
    request_id = queue(repo)
    token = LINKS_OK.token_for(request_id)
    results, errors = [], []
    barrier = threading.Barrier(6)

    def work():
        handle = None
        try:
            handle = db.reopen()
            confirm, _, _ = build(handle)
            barrier.wait(timeout=15)
            results.append(confirm.confirm(token))
        except Exception as exc:
            errors.append(exc)
        finally:
            if handle is not None:
                handle.close()

    threads = [threading.Thread(target=work) for _ in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert not any(thread.is_alive() for thread in threads)
    assert errors == []
    assert len(results) == 6 and all(r is not None and r.confirmed for r in results)
    contacts = db.service().list_contacts()
    assert len(contacts) == 1
    assert db.consent_service().status(contacts[0].id, "newsletter") == "granted"
    assert repo.get(request_id).confirmed_at == T0
