from admin_app_support import TestClient, create_app

from mini_marketo.domain.segment import Criterion

TOTAL = 30
ROW = "<td>Ana "


def setup(db):
    contacts, segments = db.service(), db.segment_service()
    for i in range(TOTAL):
        contacts.create_contact("Ana", f"N{i:02d}", f"ana{i}@a.pt")
    segment = segments.create_segment(
        "Todas", "", "all", [Criterion("first_name", "is", "ana")]
    )
    client = TestClient(create_app(service=contacts, segment_service=segments))
    return client, segment, segments


def test_default_page_size_is_25(db):
    client, segment, _ = setup(db)
    text = client.get(f"/segments/{segment.id}").text
    assert "Mostrando 1–25 de 30" in text and text.count(ROW) == 25


def test_second_page_shows_the_rest(db):
    client, segment, _ = setup(db)
    text = client.get(f"/segments/{segment.id}?page=2").text
    assert "Mostrando 26–30 de 30" in text and text.count(ROW) == 5


def test_user_can_choose_the_page_size(db):
    client, segment, _ = setup(db)
    first = client.get(f"/segments/{segment.id}?per_page=10").text
    assert "Mostrando 1–10 de 30" in first and first.count(ROW) == 10
    third = client.get(f"/segments/{segment.id}?per_page=10&page=3").text
    assert "Mostrando 21–30 de 30" in third and third.count(ROW) == 10


def test_largest_size_shows_everyone_without_navigation(db):
    client, segment, _ = setup(db)
    text = client.get(f"/segments/{segment.id}?per_page=100").text
    assert "Mostrando 1–30 de 30" in text and text.count(ROW) == 30
    assert 'rel="next"' not in text and 'rel="prev"' not in text


def test_invalid_page_size_falls_back_to_the_default(db):
    client, segment, _ = setup(db)
    for raw in ("7", "abc", "-5", ""):
        text = client.get(f"/segments/{segment.id}?per_page={raw}").text
        assert "Mostrando 1–25 de 30" in text


def test_page_out_of_range_never_fails(db):
    client, segment, _ = setup(db)
    assert "Mostrando 26–30 de 30" in client.get(f"/segments/{segment.id}?page=99").text
    for raw in ("abc", "0", "-4"):
        response = client.get(f"/segments/{segment.id}?page={raw}")
        assert response.status_code == 200 and "Mostrando 1–25 de 30" in response.text


def test_navigation_links_keep_the_page_size(db):
    client, segment, _ = setup(db)
    text = client.get(f"/segments/{segment.id}?per_page=10&page=2").text
    assert f'href="/segments/{segment.id}?per_page=10&amp;page=1"' in text
    assert f'href="/segments/{segment.id}?per_page=10&amp;page=3"' in text
    assert "Página 2 de 3" in text


def test_first_page_has_no_previous_link(db):
    client, segment, _ = setup(db)
    text = client.get(f"/segments/{segment.id}").text
    assert 'rel="prev"' not in text and 'rel="next"' in text


def test_selector_marks_the_current_size(db):
    client, segment, _ = setup(db)
    text = client.get(f"/segments/{segment.id}?per_page=50").text
    assert 'value="50" selected' in text and 'value="25" selected' not in text


def test_heading_shows_the_total_on_any_page(db):
    client, segment, _ = setup(db)
    assert "Membros (30)" in client.get(f"/segments/{segment.id}?page=2").text


def test_empty_segment_has_no_pagination_controls(db):
    client, _, segments = setup(db)
    nobody = segments.create_segment(
        "Ninguem", "", "all", [Criterion("first_name", "is", "zzz")]
    )
    text = client.get(f"/segments/{nobody.id}").text
    assert "Nenhum contacto cumpre estes critérios." in text
    assert "Mostrando" not in text and 'rel="next"' not in text
