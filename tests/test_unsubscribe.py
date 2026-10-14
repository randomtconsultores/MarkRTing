import pytest
from campaign_support import Setup

from mini_marketo.services.unsubscribe_service import UnsubscribeService, mask_email
from mini_marketo.unsubscribe import UnsubscribeLinks

LINKS = UnsubscribeLinks("https://marketing.exemplo.pt", "k" * 40)


def make_service(s, links=LINKS):
    return UnsubscribeService(s.repo, s.segments.consents, links)


def queued(s):
    c = s.campaign()
    s.service.enqueue(c.id)
    return c


@pytest.mark.parametrize(
    "email, masked",
    [("ana@a.pt", "a***@a.pt"), ("", "***"), ("sem-arroba", "***"), ("@a.pt", "***"), ("x@", "***")],
)
def test_mask_email(email, masked):
    assert mask_email(email) == masked


def test_preview_shows_the_purpose_and_a_masked_email_and_changes_nothing(db):
    s = Setup(db)
    c = queued(s)
    token = LINKS.token_for(s.message_for(c.id, s.ana).id)
    view = make_service(s).preview(token)
    assert (view.purpose, view.masked_email, view.already_withdrawn) == (
        "newsletter",
        "a***@a.pt",
        False,
    )
    assert s.consents.status(s.ana.id, "newsletter") == "granted"
    assert len(s.consents.history(s.ana.id)) == 1


def test_unsubscribe_withdraws_consent_with_the_new_source_and_the_message_email(db):
    s = Setup(db)
    c = queued(s)
    message = s.message_for(c.id, s.ana)
    view = make_service(s).unsubscribe(LINKS.token_for(message.id))
    assert view.already_withdrawn is True
    assert s.consents.status(s.ana.id, "newsletter") == "withdrawn"
    assert s.consents.is_eligible(s.ana.id, "newsletter") is False
    latest = s.consents.history(s.ana.id)[0]
    assert (latest.decision, latest.source, latest.email) == ("withdrawn", "unsubscribe", "ana@a.pt")


def test_unsubscribe_is_idempotent(db):
    s = Setup(db)
    c = queued(s)
    token = LINKS.token_for(s.message_for(c.id, s.ana).id)
    service = make_service(s)
    service.unsubscribe(token)
    again = service.unsubscribe(token)
    assert again.already_withdrawn is True
    assert len(s.consents.history(s.ana.id)) == 2
    assert service.preview(token).already_withdrawn is True


def test_only_the_purpose_of_the_campaign_is_withdrawn(db):
    s = Setup(db)
    s.consents.record(s.ana.id, "promotional", "granted", "admin")
    c = queued(s)
    make_service(s).unsubscribe(LINKS.token_for(s.message_for(c.id, s.ana).id))
    assert s.consents.status(s.ana.id, "newsletter") == "withdrawn"
    assert s.consents.status(s.ana.id, "promotional") == "granted"


def test_another_contact_is_not_affected(db):
    s = Setup(db)
    c = queued(s)
    make_service(s).unsubscribe(LINKS.token_for(s.message_for(c.id, s.ana).id))
    assert s.consents.status(s.rui.id, "newsletter") == "granted"


def test_a_later_grant_is_respected_and_a_new_link_withdraws_again(db):
    s = Setup(db)
    c = queued(s)
    token = LINKS.token_for(s.message_for(c.id, s.ana).id)
    service = make_service(s)
    service.unsubscribe(token)
    s.consents.record(s.ana.id, "newsletter", "granted", "admin")
    assert s.consents.is_eligible(s.ana.id, "newsletter") is True
    assert service.preview(token).already_withdrawn is False
    service.unsubscribe(token)
    assert s.consents.status(s.ana.id, "newsletter") == "withdrawn"


def test_a_contact_who_unsubscribed_is_skipped_by_the_next_campaign(db):
    s = Setup(db)
    c = queued(s)
    make_service(s).unsubscribe(LINKS.token_for(s.message_for(c.id, s.ana).id))
    later = s.campaign(name="Novembro")
    assert s.service.enqueue(later.id) == {"pending": 1, "skipped": 2}
    assert s.message_for(later.id, s.ana).status == "skipped"


def test_a_link_for_a_skipped_message_records_the_withdrawal_too(db):
    s = Setup(db)
    c = queued(s)
    eva = s.message_for(c.id, s.eva)
    assert eva.status == "skipped"
    make_service(s).unsubscribe(LINKS.token_for(eva.id))
    assert s.consents.status(s.eva.id, "newsletter") == "withdrawn"


def test_invalid_tokens_change_nothing_and_give_none(db):
    s = Setup(db)
    c = queued(s)
    message = s.message_for(c.id, s.ana)
    other = UnsubscribeLinks("https://marketing.exemplo.pt", "z" * 40)
    service = make_service(s)
    tokens = [
        "",
        "lixo",
        other.token_for(message.id),
        LINKS.token_for(99999),
        LINKS.token_for(message.id)[:-2] + "xx",
    ]
    for token in tokens:
        assert service.preview(token) is None
        assert service.unsubscribe(token) is None
    assert len(s.consents.history(s.ana.id)) == 1


def test_without_configured_links_every_token_is_refused(db):
    s = Setup(db)
    c = queued(s)
    token = LINKS.token_for(s.message_for(c.id, s.ana).id)
    service = make_service(s, links=None)
    assert service.preview(token) is None and service.unsubscribe(token) is None
    assert s.consents.status(s.ana.id, "newsletter") == "granted"


def test_a_token_stays_valid_after_the_secret_is_rotated(db):
    s = Setup(db)
    c = queued(s)
    token = LINKS.token_for(s.message_for(c.id, s.ana).id)
    rotated = UnsubscribeLinks(
        "https://marketing.exemplo.pt", "n" * 40, previous_secrets=("k" * 40,)
    )
    assert make_service(s, links=rotated).unsubscribe(token) is not None
    assert s.consents.status(s.ana.id, "newsletter") == "withdrawn"
