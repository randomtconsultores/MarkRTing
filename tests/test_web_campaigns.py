from campaign_support import Setup
from fastapi.testclient import TestClient

from mini_marketo.main import create_app


def form(s, **overrides):
    data = {
        "name": "Outubro",
        "subject": "Olá {first_name}",
        "body_text": "Texto",
        "body_html": "",
        "purpose": "newsletter",
        "segment_id": str(s.segment.id),
    }
    data.update(overrides)
    return data


def setup(db):
    s = Setup(db)
    client = TestClient(
        create_app(service=s.contacts, segment_service=s.segments, campaign_service=s.service)
    )
    return client, s


def test_list_page_shows_the_form_the_segments_and_the_menu_link(db):
    client, _ = setup(db)
    page = client.get("/campaigns")
    assert page.status_code == 200
    assert "Nova campanha" in page.text and "Ainda não há campanhas" in page.text
    assert 'href="/campaigns"' in page.text
    assert "Todos" in page.text


def test_create_redirects_to_the_detail_page_as_a_draft(db):
    client, s = setup(db)
    response = client.post("/campaigns", data=form(s), follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"].startswith("/campaigns/")
    page = client.get(response.headers["location"])
    assert page.status_code == 200
    assert "Outubro" in page.text and "Rascunho" in page.text
    assert "Enfileirar envio" in page.text and "Todos" in page.text
    assert 'href="/campaigns/' in client.get("/campaigns").text


def test_a_subject_with_line_breaks_is_flattened(db):
    client, s = setup(db)
    page = client.post("/campaigns", data=form(s, subject="Olá\r\nBcc: x@y.z"))
    assert page.status_code == 200 and "Olá Bcc: x@y.z" in page.text


def test_invalid_purpose_keeps_the_form_and_stores_nothing(db):
    client, s = setup(db)
    response = client.post("/campaigns", data=form(s, purpose="outra"))
    assert response.status_code == 422 and "Finalidade inválida" in response.text
    assert 'role="alert"' in response.text and 'value="Outubro"' in response.text
    assert s.service.list_campaigns() == []


def test_missing_or_unknown_segment_is_reported(db):
    client, s = setup(db)
    not_a_number = client.post("/campaigns", data=form(s, segment_id="abc"))
    assert not_a_number.status_code == 422 and "Escolha um segmento" in not_a_number.text
    unknown = client.post("/campaigns", data=form(s, segment_id="9999"))
    assert unknown.status_code == 422 and "O segmento não existe" in unknown.text
    assert s.service.list_campaigns() == []


def test_duplicate_name_is_reported(db):
    client, s = setup(db)
    s.campaign(name="Outubro")
    response = client.post("/campaigns", data=form(s, name="outubro"))
    assert response.status_code == 422 and "Já existe uma campanha" in response.text


def test_names_are_escaped_in_the_pages(db):
    client, s = setup(db)
    c = s.campaign(name="<script>alert(1)</script>")
    for url in ("/campaigns", f"/campaigns/{c.id}"):
        text = client.get(url).text
        assert "&lt;script&gt;" in text and "<script>alert" not in text


def test_enqueue_shows_counts_and_the_skipped_contact_and_hides_the_button(db):
    client, s = setup(db)
    c = s.campaign()
    page = client.post(f"/campaigns/{c.id}/enqueue")
    assert page.status_code == 200
    assert "Enfileirada" in page.text and "Enfileirar envio" not in page.text
    assert "Pendente" in page.text and "Ignorado" in page.text
    assert "eva@a.pt" in page.text and "Sem consentimento concedido" in page.text
    assert s.repo.counts(c.id) == {"pending": 2, "skipped": 1}


def test_a_second_enqueue_is_rejected(db):
    client, s = setup(db)
    c = s.campaign()
    client.post(f"/campaigns/{c.id}/enqueue")
    again = client.post(f"/campaigns/{c.id}/enqueue")
    assert again.status_code == 422 and "já foi enfileirada" in again.text
    assert s.repo.counts(c.id) == {"pending": 2, "skipped": 1}


def test_a_campaign_whose_segment_was_deleted_cannot_be_enqueued(db):
    client, s = setup(db)
    c = s.campaign()
    s.segments.delete_segment(s.segment.id)
    page = client.get(f"/campaigns/{c.id}")
    assert page.status_code == 200 and "já não existe" in page.text
    response = client.post(f"/campaigns/{c.id}/enqueue")
    assert response.status_code == 422 and "já não existe" in response.text


def test_unknown_campaign_is_404(db):
    client, _ = setup(db)
    assert client.get("/campaigns/999").status_code == 404
    assert client.post("/campaigns/999/enqueue").status_code == 404
