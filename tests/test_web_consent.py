from fastapi.testclient import TestClient

from mini_marketo.main import create_app

NO_RECORD = '<span class="badge">Sem registo</span>'
GRANTED = '<span class="badge">Concedido</span>'
WITHDRAWN = '<span class="badge">Retirado</span>'
NOTE = "não é consentimento"


def setup(db):
    contacts, consents = db.service(), db.consent_service()
    client = TestClient(create_app(service=contacts, consent_service=consents))
    ana = contacts.create_contact("Ana", "Silva", "ana@a.pt")
    return client, consents, ana


def test_page_shows_no_record_for_every_purpose(db):
    client, _, ana = setup(db)
    response = client.get(f"/contacts/{ana.id}/consent")
    assert response.status_code == 200
    assert response.text.count(NO_RECORD) == 2
    assert NOTE in response.text
    assert "Última decisão (UTC)" in response.text
    assert "Ainda não há decisões registadas" in response.text


def test_grant_redirects_and_shows_granted(db):
    client, consents, ana = setup(db)
    response = client.post(
        f"/contacts/{ana.id}/consent",
        data={"purpose": "newsletter", "decision": "granted"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == f"/contacts/{ana.id}/consent"
    page = client.get(response.headers["location"])
    assert GRANTED in page.text
    assert page.text.count(NO_RECORD) == 1
    assert consents.status(ana.id, "promotional") == "none"


def test_withdrawal_shows_withdrawn_and_keeps_both_records(db):
    client, consents, ana = setup(db)
    url = f"/contacts/{ana.id}/consent"
    client.post(url, data={"purpose": "newsletter", "decision": "granted"})
    page = client.post(url, data={"purpose": "newsletter", "decision": "withdrawn"})
    assert page.status_code == 200
    assert WITHDRAWN in page.text
    assert GRANTED not in page.text
    assert "Histórico (2)" in page.text
    assert len(consents.history(ana.id)) == 2


def test_note_is_hidden_when_every_purpose_has_a_record(db):
    client, _, ana = setup(db)
    url = f"/contacts/{ana.id}/consent"
    client.post(url, data={"purpose": "newsletter", "decision": "granted"})
    page = client.post(url, data={"purpose": "promotional", "decision": "withdrawn"})
    assert NO_RECORD not in page.text
    assert NOTE not in page.text


def test_origin_is_fixed_by_the_server(db):
    client, consents, ana = setup(db)
    page = client.post(
        f"/contacts/{ana.id}/consent",
        data={"purpose": "newsletter", "decision": "granted", "source": "form"},
    )
    assert consents.history(ana.id)[0].source == "admin"
    assert "Ação administrativa" in page.text


def test_invalid_purpose_shows_error_and_stores_nothing(db):
    client, consents, ana = setup(db)
    response = client.post(
        f"/contacts/{ana.id}/consent", data={"purpose": "outra", "decision": "granted"}
    )
    assert response.status_code == 422 and "Finalidade inválida" in response.text
    assert 'role="alert"' in response.text
    assert consents.history(ana.id) == []


def test_invalid_decision_shows_error_and_stores_nothing(db):
    client, consents, ana = setup(db)
    response = client.post(
        f"/contacts/{ana.id}/consent", data={"purpose": "newsletter", "decision": "talvez"}
    )
    assert response.status_code == 422 and "Decisão inválida" in response.text
    assert consents.history(ana.id) == []


def test_unknown_contact_is_404(db):
    client, _, _ = setup(db)
    assert client.get("/contacts/999/consent").status_code == 404
    response = client.post(
        "/contacts/999/consent", data={"purpose": "newsletter", "decision": "granted"}
    )
    assert response.status_code == 404


def test_contacts_list_links_to_the_consent_page(db):
    client, _, ana = setup(db)
    assert f'href="/contacts/{ana.id}/consent"' in client.get("/contacts").text
