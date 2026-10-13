from fastapi.testclient import TestClient

from mini_marketo.main import create_app


def setup(db):
    contacts, segments = db.service(), db.segment_service()
    client = TestClient(create_app(service=contacts, segment_service=segments))
    return client, contacts, segments


def seg_data(**over):
    data = {
        "name": "ACME",
        "description": "",
        "match_mode": "all",
        "criterion_field": ["company"] + [""] * 9,
        "criterion_operator": ["is"] * 10,
        "criterion_value": ["acme"] + [""] * 9,
    }
    data.update(over)
    return data


def test_segments_page_loads(db):
    client, _, _ = setup(db)
    response = client.get("/segments")
    assert response.status_code == 200 and "Novo segmento" in response.text


def test_create_redirects_to_the_detail_page(db):
    client, _, _ = setup(db)
    response = client.post("/segments", data=seg_data(), follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"].startswith("/segments/")
    detail = client.get(response.headers["location"])
    assert detail.status_code == 200 and "ACME" in detail.text


def test_blank_rows_are_ignored(db):
    client, _, segments = setup(db)
    assert client.post("/segments", data=seg_data()).status_code == 200
    assert len(segments.list_segments()[0].criteria) == 1


def test_no_criteria_shows_error_and_keeps_the_form(db):
    client, _, segments = setup(db)
    response = client.post(
        "/segments", data=seg_data(criterion_field=[""] * 10, criterion_value=[""] * 10)
    )
    assert response.status_code == 422 and "precisa de 1 a 20" in response.text
    assert 'value="ACME"' in response.text
    assert segments.list_segments() == []


def test_operator_must_fit_the_field(db):
    client, _, _ = setup(db)
    data = seg_data(
        criterion_field=["first_name"] + [""] * 9,
        criterion_operator=["before"] + ["is"] * 9,
        criterion_value=["2026-01-01"] + [""] * 9,
    )
    response = client.post("/segments", data=data)
    assert response.status_code == 422 and "não se aplica" in response.text


def test_invalid_date_shows_error(db):
    client, _, _ = setup(db)
    data = seg_data(
        criterion_field=["created_at"] + [""] * 9,
        criterion_operator=["after"] + ["is"] * 9,
        criterion_value=["13/10/2026"] + [""] * 9,
    )
    response = client.post("/segments", data=data)
    assert response.status_code == 422 and "Data inválida" in response.text


def test_duplicate_name_shows_error(db):
    client, _, _ = setup(db)
    client.post("/segments", data=seg_data())
    response = client.post("/segments", data=seg_data(name="acme"))
    assert response.status_code == 422 and "Já existe" in response.text


def test_ragged_form_is_rejected(db):
    client, _, _ = setup(db)
    response = client.post("/segments", data=seg_data(criterion_operator=["is"]))
    assert response.status_code == 422 and "inválido" in response.text


def test_detail_lists_only_the_members(db):
    client, contacts, _ = setup(db)
    acme = contacts.create_company("ACME")
    contacts.create_contact("Ana", "Silva", "ana@a.pt", acme.id)
    contacts.create_contact("Rui", "Costa", "rui@a.pt")
    response = client.post("/segments", data=seg_data())
    assert response.status_code == 200
    assert "Ana" in response.text and "Rui" not in response.text


def test_list_shows_the_member_count(db):
    client, contacts, _ = setup(db)
    acme = contacts.create_company("ACME")
    contacts.create_contact("Ana", "Silva", "ana@a.pt", acme.id)
    client.post("/segments", data=seg_data())
    assert "<td>1</td>" in client.get("/segments").text


def test_any_mode_and_description_are_shown(db):
    client, _, _ = setup(db)
    response = client.post(
        "/segments", data=seg_data(match_mode="any", description="Descrição X")
    )
    assert "qualquer um" in response.text and "Descrição X" in response.text


def test_values_are_escaped(db):
    client, _, _ = setup(db)
    data = seg_data(
        criterion_field=["first_name"] + [""] * 9,
        criterion_operator=["contains"] + ["is"] * 9,
        criterion_value=["<script>alert(1)</script>"] + [""] * 9,
    )
    response = client.post("/segments", data=data)
    assert response.status_code == 200
    assert "<script>alert(1)" not in response.text and "&lt;script&gt;" in response.text


def test_unknown_segment_is_404(db):
    client, _, _ = setup(db)
    assert client.get("/segments/999").status_code == 404
    assert client.post("/segments/999/delete").status_code == 404


def test_delete_removes_the_segment(db):
    client, _, segments = setup(db)
    created = client.post("/segments", data=seg_data(), follow_redirects=False)
    location = created.headers["location"]
    response = client.post(location + "/delete")
    assert response.status_code == 200 and "Ainda não há segmentos" in response.text
    assert segments.list_segments() == []
    assert client.get(location).status_code == 404
