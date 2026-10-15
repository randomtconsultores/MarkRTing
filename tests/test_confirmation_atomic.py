import pytest

from test_subscription import T0
from test_subscription_confirm import LINKS_OK, build
from test_subscription_delivery import queue


@pytest.mark.parametrize("failure", ["second_consent", "confirmation_stamp"])
@pytest.mark.parametrize("existing", [False, True])
def test_failure_rolls_back_all_confirmation_writes(db, monkeypatch, failure, existing):
    if existing:
        db.service().create_contact("Nome", "Existente", "a@a.pt")
    confirm, repo, _ = build(db)
    request_id = queue(repo, purposes=("newsletter", "promotional"))
    original = confirm.writer.execute
    grants = []
    def fail(sql, params=()):
        if sql.startswith("INSERT INTO consent_events"):
            grants.append(params[1])
            if failure == "second_consent" and len(grants) == 2:
                raise RuntimeError("falha simulada")
        if failure == "confirmation_stamp" and sql.startswith("UPDATE subscription_requests"):
            raise RuntimeError("falha simulada")
        return original(sql, params)
    monkeypatch.setattr(confirm.writer, "execute", fail)
    with pytest.raises(RuntimeError, match="falha simulada"):
        confirm.confirm(LINKS_OK.token_for(request_id))
    assert db.scalar("SELECT count(*) FROM contacts") == int(existing)
    assert db.scalar("SELECT count(*) FROM contact_emails") == int(existing)
    assert db.scalar("SELECT count(*) FROM consent_events") == 0
    assert repo.get(request_id).confirmed_at is None
    monkeypatch.setattr(confirm.writer, "execute", original)
    assert confirm.confirm(LINKS_OK.token_for(request_id)).confirmed
    assert db.scalar("SELECT count(*) FROM contacts") == 1
    assert db.scalar("SELECT count(*) FROM consent_events") == 2
    assert repo.get(request_id).confirmed_at == T0


def test_reusing_confirmed_token_does_not_override_later_withdrawal(db):
    confirm, repo, _ = build(db)
    request_id = queue(repo)
    token = LINKS_OK.token_for(request_id)
    confirm.confirm(token)
    contact = db.service().list_contacts()[0]
    db.consent_service().record(contact.id, "newsletter", "withdrawn", "unsubscribe")
    assert confirm.confirm(token).confirmed
    assert db.consent_service().status(contact.id, "newsletter") == "withdrawn"
    assert db.scalar("SELECT count(*) FROM consent_events") == 2
