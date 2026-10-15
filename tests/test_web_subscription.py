from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from test_delivery import LINKS, clear_environment
from test_subscription import T0, make
from test_subscription_confirm import TTL, build

from mini_marketo.main import create_app
from mini_marketo.subscription_links import SubscriptionLinks

LINKS_OK = SubscriptionLinks(LINKS)
RECEIVED = (303, "/subscribe/received")


def form(**overrides):
    data = {
        "first_name": "Ana", "last_name": "Silva", "email": "ana@a.pt",
        "purposes": ["newsletter", "promotional"], "lang": "pt", "website": "",
    }
    data.update(overrides)
    return data


def setup(db, confirm_now=T0, privacy_url=""):
    service, repo, suppressions = make(db)
    confirm, _, _ = build(db, confirm_now)
    app = create_app(
        service=db.service(), subscription_service=service, confirmation_service=confirm,
        suppression_service=suppressions, events_token="", privacy_url=privacy_url,
    )
    return TestClient(app), repo, suppressions


def count(db):
    return db.scalar("SELECT count(*) FROM subscription_requests")


def last_token(db):
    return LINKS_OK.token_for(db.scalar("SELECT max(id) FROM subscription_requests"))


def submit(client, **overrides):
    response = client.post("/subscribe", data=form(**overrides), follow_redirects=False)
    return response.status_code, response.headers.get("location")


# --- formulário ---


def test_the_form_is_standalone_and_nothing_is_pre_checked(db):
    client, _, _ = setup(db)
    page = client.get("/subscribe")
    assert page.status_code == 200
    text = page.text
    assert 'action="/subscribe"' in text and 'name="first_name"' in text
    assert 'name="purposes" value="newsletter"' in text
    assert 'name="purposes" value="promotional"' in text
    assert " checked" not in text
    assert 'name="website"' in text and 'aria-hidden="true"' in text
    assert "noindex" in text and 'href="/contacts"' not in text
    assert "Política de privacidade" not in text
    assert page.headers["referrer-policy"] == "no-referrer"
    assert page.headers["cache-control"] == "no-store"


def test_the_privacy_link_is_shown_when_configured(db):
    client, _, _ = setup(db, privacy_url="https://x.pt/privacidade")
    text = client.get("/subscribe").text
    assert 'href="https://x.pt/privacidade"' in text and "Política de privacidade" in text


def test_an_unknown_language_falls_back_to_the_default(db):
    client, _, _ = setup(db)
    assert 'lang="pt"' in client.get("/subscribe?lang=xx").text


def test_a_valid_request_is_stored_without_creating_a_contact_or_consent(db):
    client, repo, _ = setup(db)
    assert submit(client) == RECEIVED
    assert count(db) == 1
    stored = repo.get(db.scalar("SELECT max(id) FROM subscription_requests"))
    assert stored.email == "ana@a.pt" and stored.purposes == ("newsletter", "promotional")
    assert db.service().list_contacts() == []
    assert db.scalar("SELECT count(*) FROM consent_events") == 0
    page = client.get("/subscribe/received")
    assert page.status_code == 200 and "48 horas" in page.text and "Verifique o seu email" in page.text


def test_a_single_purpose_can_be_chosen(db):
    client, repo, _ = setup(db)
    assert submit(client, purposes=["promotional"]) == RECEIVED
    assert repo.get(db.scalar("SELECT max(id) FROM subscription_requests")).purposes == ("promotional",)


def test_every_valid_request_gets_the_same_answer(db):
    client, _, suppressions = setup(db)
    suppressions.add("sup@a.pt", "hard_bounce", "smtp", "")
    db.service().create_contact("Eva", "Lopes", "eva@a.pt")
    outcomes = [
        submit(client, email=email)
        for email in ("ana@a.pt", "sup@a.pt", "ana@a.pt", "ana@a.pt", "ana@a.pt", "eva@a.pt")
    ]
    outcomes.append(submit(client, website="http://spam.exemplo"))
    assert set(outcomes) == {RECEIVED}
    # ana: 3 gravados e o 4.º limitado; sup: suprimido; eva: já conhecida; robô: ignorado.
    assert count(db) == 4


@pytest.mark.parametrize(
    "overrides",
    [
        {"purposes": []}, {"purposes": ["outra"]}, {"email": "sem-arroba"},
        {"first_name": " "}, {"last_name": ""}, {"first_name": "x" * 101},
    ],
)
def test_an_invalid_form_is_rejected_and_stores_nothing(db, overrides):
    client, _, _ = setup(db)
    response = client.post("/subscribe", data=form(**overrides), follow_redirects=False)
    assert response.status_code == 422
    assert "Não foi possível processar o pedido" in response.text
    assert 'role="alert"' in response.text
    assert count(db) == 0


def test_an_invalid_form_keeps_what_was_typed_and_escapes_it(db):
    client, _, _ = setup(db)
    response = client.post(
        "/subscribe",
        data=form(email='"><script>alert(1)</script>', purposes=["newsletter", "outra"]),
        follow_redirects=False,
    )
    assert response.status_code == 422
    assert "<script>alert" not in response.text and "&lt;script&gt;" in response.text
    assert 'value="newsletter" checked' in response.text
    assert 'value="promotional" checked' not in response.text
    assert 'value="Ana"' in response.text


# --- confirmação ---


def test_confirming_through_the_pages(db):
    client, _, _ = setup(db)
    submit(client, purposes=["newsletter"])
    token = last_token(db)
    page = client.get(f"/subscribe/confirm/{token}")
    assert page.status_code == 200
    assert "Confirmar subscrição" in page.text and "Newsletter" in page.text
    assert f'action="/subscribe/confirm/{token}"' in page.text
    assert page.headers["referrer-policy"] == "no-referrer"
    assert page.headers["cache-control"] == "no-store" and "noindex" in page.text
    assert db.service().list_contacts() == []  # o GET não confirma nada
    done = client.post(f"/subscribe/confirm/{token}")
    assert done.status_code == 200
    assert "Subscrição confirmada" in done.text and "Newsletter" in done.text
    contacts = db.service().list_contacts()
    assert [c.emails for c in contacts] == [("ana@a.pt",)]
    assert db.consent_service().status(contacts[0].id, "newsletter") == "granted"
    again = client.get(f"/subscribe/confirm/{token}")
    assert again.status_code == 200 and "Subscrição confirmada" in again.text
    assert "<button" not in again.text


@pytest.mark.parametrize("token", ["1.abcdefghijklmnopqrstuv", "x", "999.abcdefghijklmnopqrstuv"])
def test_an_invalid_link_gives_the_same_404_and_confirms_nothing(db, token):
    client, _, _ = setup(db)
    submit(client)
    for response in (client.get(f"/subscribe/confirm/{token}"), client.post(f"/subscribe/confirm/{token}")):
        assert response.status_code == 404
        assert "Ligação inválida ou expirada" in response.text
    assert db.service().list_contacts() == []


def test_a_link_for_an_unknown_request_gives_the_same_404(db):
    client, _, _ = setup(db)
    response = client.get(f"/subscribe/confirm/{LINKS_OK.token_for(999)}")
    assert response.status_code == 404 and "Ligação inválida ou expirada" in response.text


def test_an_expired_link_gives_the_same_404(db):
    client, _, _ = setup(db, confirm_now=T0 + TTL)
    submit(client)
    token = last_token(db)
    for response in (client.get(f"/subscribe/confirm/{token}"), client.post(f"/subscribe/confirm/{token}")):
        assert response.status_code == 404
    assert db.service().list_contacts() == []


def test_an_address_suppressed_after_the_request_cannot_be_confirmed(db):
    client, _, suppressions = setup(db)
    submit(client)
    token = last_token(db)
    suppressions.add("ana@a.pt", "complaint", "event", "")
    assert client.get(f"/subscribe/confirm/{token}").status_code == 404
    assert client.post(f"/subscribe/confirm/{token}").status_code == 404
    assert db.service().list_contacts() == []


# --- com as fábricas reais (SQLite em ficheiro) ---


def real_environment(monkeypatch, configured=True):
    clear_environment(monkeypatch)
    for name in ("MINI_MARKETO_EVENTS_TOKEN", "MINI_MARKETO_PRIVACY_URL", "MINI_MARKETO_DATABASE_URL"):
        monkeypatch.delenv(name, raising=False)
    if configured:
        monkeypatch.setenv("MINI_MARKETO_PUBLIC_URL", "https://marketing.exemplo.pt")
        monkeypatch.setenv("MINI_MARKETO_SECRET_KEY", "k" * 40)


def test_the_public_flow_works_end_to_end_with_the_real_factories(tmp_path, monkeypatch):
    from mini_marketo.database import connect
    from mini_marketo.repositories.contact_repository import ContactRepository

    real_environment(monkeypatch)
    path = tmp_path / "web.db"
    client = TestClient(create_app(db_path=path))
    assert submit(client, purposes=["promotional"]) == RECEIVED
    conn = connect(path)
    try:
        request_id = conn.execute("SELECT max(id) FROM subscription_requests").fetchone()[0]
        assert ContactRepository(conn).list_contacts() == []
    finally:
        conn.close()
    token = LINKS_OK.token_for(request_id)
    assert client.get(f"/subscribe/confirm/{token}").status_code == 200
    assert "Subscrição confirmada" in client.post(f"/subscribe/confirm/{token}").text
    conn = connect(path)
    try:
        contacts = ContactRepository(conn).list_contacts()
        assert [c.emails for c in contacts] == [("ana@a.pt",)]
        rows = conn.execute("SELECT purpose, decision, source FROM consent_events").fetchall()
        assert [tuple(r) for r in rows] == [("promotional", "granted", "form")]
    finally:
        conn.close()


def test_without_the_public_url_no_confirmation_is_accepted_but_requests_are(tmp_path, monkeypatch):
    real_environment(monkeypatch, configured=False)
    client = TestClient(create_app(db_path=tmp_path / "web.db"))
    assert client.get("/subscribe/confirm/1.abcdefghijklmnopqrstuv").status_code == 404
    assert client.post("/subscribe/confirm/1.abcdefghijklmnopqrstuv").status_code == 404
    assert submit(client) == RECEIVED
