from fastapi.testclient import TestClient

from mini_marketo.domain.segment import Criterion
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


def create(client, **over):
    response = client.post("/segments", data=seg_data(**over), follow_redirects=False)
    assert response.status_code == 303
    return response.headers["location"]


def test_edit_form_is_prefilled(db):
    client, _, _ = setup(db)
    location = create(client)
    response = client.get(location + "/edit")
    assert response.status_code == 200
    assert 'value="ACME"' in response.text and 'value="acme"' in response.text
    assert 'value="company" selected' in response.text


def test_edit_changes_the_segment(db):
    client, _, segments = setup(db)
    location = create(client)
    data = seg_data(
        name="Outro",
        match_mode="any",
        criterion_field=["first_name"] + [""] * 9,
        criterion_operator=["starts_with"] + ["is"] * 9,
        criterion_value=["a"] + [""] * 9,
    )
    response = client.post(location + "/edit", data=data)
    assert response.status_code == 200
    assert "Outro" in response.text and "qualquer um" in response.text
    stored = segments.list_segments()[0]
    assert stored.name == "Outro" and stored.criteria == (Criterion("first_name", "starts_with", "a"),)


def test_edit_can_keep_the_same_name(db):
    client, _, _ = setup(db)
    location = create(client)
    response = client.post(location + "/edit", data=seg_data(description="Nova descrição"))
    assert response.status_code == 200 and "Nova descrição" in response.text


def test_edit_error_keeps_the_input_and_the_original(db):
    client, _, segments = setup(db)
    location = create(client)
    data = seg_data(name="Novo nome", criterion_field=[""] * 10, criterion_value=[""] * 10)
    response = client.post(location + "/edit", data=data)
    assert response.status_code == 422 and "precisa de 1 a 20" in response.text
    assert 'value="Novo nome"' in response.text
    assert segments.list_segments()[0].name == "ACME"


def test_edit_to_a_duplicate_name_is_rejected(db):
    client, _, _ = setup(db)
    create(client, name="Primeiro")
    location = create(client, name="Segundo")
    response = client.post(location + "/edit", data=seg_data(name="primeiro"))
    assert response.status_code == 422 and "Já existe" in response.text


def test_edit_of_an_unknown_segment_is_404(db):
    client, _, _ = setup(db)
    assert client.get("/segments/999/edit").status_code == 404
    assert client.post("/segments/999/edit", data=seg_data()).status_code == 404


def test_detail_links_to_the_edit_page(db):
    client, _, _ = setup(db)
    location = create(client)
    assert f'href="{location}/edit"' in client.get(location).text


def test_edit_page_keeps_every_criterion(db):
    client, _, segments = setup(db)
    criteria = [Criterion("first_name", "contains", str(i)) for i in range(12)]
    created = segments.create_segment("Grande", "", "all", criteria)
    edit = client.get(f"/segments/{created.id}/edit")
    assert edit.text.count('name="criterion_field"') == 12


def test_new_segment_form_still_has_ten_rows(db):
    client, _, _ = setup(db)
    assert client.get("/segments").text.count('name="criterion_field"') == 10
