import pytest
from campaign_support import Setup
from fastapi.testclient import TestClient

from mini_marketo.main import create_app
from mini_marketo.services.unsubscribe_service import UnsubscribeService
from mini_marketo.unsubscribe import UnsubscribeLinks

LINKS = UnsubscribeLinks("https://marketing.exemplo.pt", "k" * 40)


def setup(db):
    s = Setup(db)
    unsub = UnsubscribeService(s.repo, s.segments.consents, LINKS)
    client = TestClient(
        create_app(
            service=s.contacts,
            segment_service=s.segments,
            campaign_service=s.service,
            unsubscribe_service=unsub,
        )
    )
    c = s.campaign()
    s.service.enqueue(c.id)
    message = s.message_for(c.id, s.ana)
    return client, s, f"/unsubscribe/{LINKS.token_for(message.id)}"


def test_get_shows_the_confirmation_without_unsubscribing(db):
    client, s, url = setup(db)
    page = client.get(url)
    assert page.status_code == 200
    assert "Cancelar subscrição" in page.text and "a***@a.pt" in page.text
    assert "ana@a.pt" not in page.text and "Newsletter" in page.text
    assert f'action="{url}"' in page.text
    assert s.consents.status(s.ana.id, "newsletter") == "granted"


def test_the_public_page_has_no_admin_menu_and_does_not_leak_the_token(db):
    client, _, url = setup(db)
    page = client.get(url)
    assert 'href="/contacts"' not in page.text and 'href="/segments"' not in page.text
    assert 'href="/campaigns"' not in page.text
    assert 'name="robots"' in page.text
    assert page.headers["referrer-policy"] == "no-referrer"
    assert page.headers["cache-control"] == "no-store"


def test_post_unsubscribes_and_answers_200_without_redirecting(db):
    client, s, url = setup(db)
    response = client.post(url, follow_redirects=False)
    assert response.status_code == 200 and "location" not in response.headers
    assert "Subscrição cancelada" in response.text and "a***@a.pt" in response.text
    assert response.headers["referrer-policy"] == "no-referrer"
    assert s.consents.status(s.ana.id, "newsletter") == "withdrawn"
    assert s.consents.history(s.ana.id)[0].source == "unsubscribe"


def test_the_one_click_post_of_rfc_8058_works(db):
    client, s, url = setup(db)
    response = client.post(
        url, data={"List-Unsubscribe": "One-Click"}, follow_redirects=False
    )
    assert response.status_code == 200
    assert s.consents.status(s.ana.id, "newsletter") == "withdrawn"


def test_posting_twice_is_harmless(db):
    client, s, url = setup(db)
    assert client.post(url).status_code == 200
    assert client.post(url).status_code == 200
    assert len(s.consents.history(s.ana.id)) == 2


def test_get_after_unsubscribing_says_it_is_already_cancelled(db):
    client, _, url = setup(db)
    client.post(url)
    page = client.get(url)
    assert page.status_code == 200 and "já cancelada" in page.text
    assert "<button" not in page.text


@pytest.mark.parametrize(
    "token",
    ["lixo", "1.AAAAAAAAAAAAAAAAAAAAAA", "99999.AAAAAAAAAAAAAAAAAAAAAA", "<script>alert(1)"],
)
def test_invalid_links_get_the_same_generic_404(db, token):
    client, s, _ = setup(db)
    for response in (client.get(f"/unsubscribe/{token}"), client.post(f"/unsubscribe/{token}")):
        assert response.status_code == 404
        assert "inválida ou expirada" in response.text
        assert "<script>alert" not in response.text
    assert s.consents.status(s.ana.id, "newsletter") == "granted"


@pytest.mark.parametrize(
    "path",
    ["<script>alert(1)</script>", "a/b", "%3Cscript%3Ealert(1)%3C%2Fscript%3E"],
)
def test_paths_that_do_not_match_the_route_get_a_404_and_reflect_nothing(db, path):
    # Um token com barra nem chega à rota: o 404 é o do próprio servidor, sem eco do pedido.
    client, s, _ = setup(db)
    for response in (client.get(f"/unsubscribe/{path}"), client.post(f"/unsubscribe/{path}")):
        assert response.status_code == 404
        assert "<script>alert" not in response.text
    assert s.consents.status(s.ana.id, "newsletter") == "granted"


def test_a_tampered_token_is_refused(db):
    client, s, url = setup(db)
    tampered = url[:-1] + ("A" if url[-1] != "A" else "B")
    assert client.post(tampered).status_code == 404
    assert s.consents.status(s.ana.id, "newsletter") == "granted"


def test_without_configured_links_the_endpoint_answers_404(db):
    s = Setup(db)
    c = s.campaign()
    s.service.enqueue(c.id)
    unsub = UnsubscribeService(s.repo, s.segments.consents, None)
    client = TestClient(
        create_app(
            service=s.contacts,
            segment_service=s.segments,
            campaign_service=s.service,
            unsubscribe_service=unsub,
        )
    )
    url = f"/unsubscribe/{LINKS.token_for(s.message_for(c.id, s.ana).id)}"
    assert client.get(url).status_code == 404 and client.post(url).status_code == 404


def test_the_existing_admin_pages_still_work_with_the_new_service(db):
    client, _, _ = setup(db)
    assert client.get("/campaigns").status_code == 200
