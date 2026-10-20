from admin_app_support import TestClient, create_app

from mini_marketo.domain.segment import Criterion


def form(value="concedido", field="consent_newsletter", operator="is"):
    return {
        "name": "Com newsletter",
        "description": "",
        "match_mode": "all",
        "criterion_field": [field] + [""] * 9,
        "criterion_operator": [operator] * 10,
        "criterion_value": [value] + [""] * 9,
    }


def setup(db):
    contacts, segments, consents = db.service(), db.segment_service(), db.consent_service()
    client = TestClient(
        create_app(service=contacts, segment_service=segments, consent_service=consents)
    )
    ana = contacts.create_contact("Ana", "Silva", "ana@a.pt")
    contacts.create_contact("Rui", "Costa", "rui@a.pt")
    consents.record(ana.id, "newsletter", "granted", "admin")
    return client, segments, consents, ana


def test_create_segment_by_consent_with_the_portuguese_term(db):
    client, *_ = setup(db)
    page = client.post("/segments", data=form())
    assert page.status_code == 200
    assert "Ana Silva" in page.text
    assert "Rui" not in page.text
    assert "Consentimento: Newsletter" in page.text
    assert "<code>Concedido</code>" in page.text
    assert "não é autorização" in page.text


def test_internal_key_is_also_accepted(db):
    client, *_ = setup(db)
    page = client.post("/segments", data=form(value="granted"))
    assert "Ana Silva" in page.text
    assert "<code>Concedido</code>" in page.text


def test_invalid_state_shows_an_error_and_stores_nothing(db):
    client, segments, *_ = setup(db)
    response = client.post("/segments", data=form(value="talvez"))
    assert response.status_code == 422
    assert "Estado de consentimento inválido" in response.text
    assert segments.list_segments() == []


def test_a_withdrawal_removes_the_contact_from_the_page(db):
    client, segments, consents, ana = setup(db)
    created = client.post("/segments", data=form(), follow_redirects=False)
    url = created.headers["location"]
    assert "Ana Silva" in client.get(url).text
    consents.record(ana.id, "newsletter", "withdrawn", "admin")
    page = client.get(url)
    assert "Ana Silva" not in page.text
    assert "Nenhum contacto cumpre estes critérios" in page.text


def test_edit_form_shows_the_portuguese_term_and_accepts_it_back(db):
    client, segments, *_ = setup(db)
    created = segments.create_segment(
        "S", "", "all", [Criterion("consent_newsletter", "is", "granted")]
    )
    edit_url = f"/segments/{created.id}/edit"
    assert 'value="Concedido"' in client.get(edit_url).text
    response = client.post(edit_url, data=form(value="Concedido"), follow_redirects=False)
    assert response.status_code == 303
    assert segments.get_segment(created.id).criteria[0].value == "granted"


def test_segments_list_evaluates_consent_segments(db):
    client, segments, *_ = setup(db)
    segments.create_segment("S", "", "all", [Criterion("consent_newsletter", "is", "granted")])
    assert client.get("/segments").status_code == 200
