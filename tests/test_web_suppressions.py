import pytest
from campaign_support import Setup
from admin_app_support import TestClient, create_app

from mini_marketo.mailer import ConfigError
from mini_marketo.main import load_events_token

TOKEN = "e" * 40
AUTH = {"Authorization": f"Bearer {TOKEN}"}


def setup(db, token=TOKEN):
    s = Setup(db)
    app = create_app(service=s.contacts, suppression_service=s.suppressions, events_token=token)
    return TestClient(app), s


def count(s):
    return len(s.suppressions.list())


# --- página de administração ---


def test_the_page_shows_the_form_the_menu_link_and_the_empty_state(db):
    client, _ = setup(db)
    page = client.get("/suppressions")
    assert page.status_code == 200
    assert 'href="/suppressions"' in page.text and "Ainda não há endereços suprimidos" in page.text
    assert 'action="/suppressions"' in page.text


def test_a_manual_suppression_is_added_and_listed(db):
    client, s = setup(db)
    response = client.post(
        "/suppressions", data={"email": " Ana@A.pt ", "detail": "pediu por telefone"},
        follow_redirects=False,
    )
    assert response.status_code == 303 and response.headers["location"] == "/suppressions"
    assert s.suppressions.is_suppressed("ana@a.pt")
    page = client.get("/suppressions")
    assert "ana@a.pt" in page.text and "pediu por telefone" in page.text


def test_an_invalid_email_keeps_the_form_and_stores_nothing(db):
    client, s = setup(db)
    response = client.post("/suppressions", data={"email": "sem-arroba", "detail": "x"})
    assert response.status_code == 422 and "Email inválido" in response.text
    assert 'value="sem-arroba"' in response.text and 'role="alert"' in response.text
    assert count(s) == 0


def test_a_manual_suppression_can_be_removed_from_the_page(db):
    client, s = setup(db)
    s.suppressions.add("ana@a.pt", "manual", "admin", "")
    assert 'action="/suppressions/remove"' in client.get("/suppressions").text
    response = client.post("/suppressions/remove", data={"email": "ana@a.pt"}, follow_redirects=False)
    assert response.status_code == 303
    assert not s.suppressions.is_suppressed("ana@a.pt")


def test_a_bounce_cannot_be_removed_from_the_page(db):
    client, s = setup(db)
    s.suppressions.add("ana@a.pt", "hard_bounce", "smtp", "550 5.1.1")
    page = client.get("/suppressions")
    assert "ana@a.pt" in page.text and 'action="/suppressions/remove"' not in page.text
    response = client.post("/suppressions/remove", data={"email": "ana@a.pt"})
    assert response.status_code == 422 and "definitivos" in response.text
    assert s.suppressions.is_suppressed("ana@a.pt")


def test_removing_an_address_that_is_not_suppressed_is_reported(db):
    client, _ = setup(db)
    response = client.post("/suppressions/remove", data={"email": "nao@existe.pt"})
    assert response.status_code == 422 and "não suprimido" in response.text


def test_details_are_escaped_in_the_page(db):
    client, s = setup(db)
    s.suppressions.add("ana@a.pt", "hard_bounce", "event", "<script>alert(1)</script>")
    text = client.get("/suppressions").text
    assert "&lt;script&gt;" in text and "<script>alert" not in text


# --- endpoint de eventos ---


def test_the_endpoint_does_not_exist_without_a_token(db):
    client, s = setup(db, token="")
    response = client.post(
        "/events/email", json={"type": "complaint", "email": "a@a.pt"}, headers=AUTH
    )
    assert response.status_code == 404
    assert count(s) == 0


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Authorization": "Bearer " + "x" * 40},
        {"Authorization": "Basic " + TOKEN},
        {"Authorization": TOKEN},
        {"Authorization": "Bearer"},
    ],
)
def test_a_missing_or_wrong_token_is_rejected_and_writes_nothing(db, headers):
    client, s = setup(db)
    response = client.post(
        "/events/email", json={"type": "complaint", "email": "a@a.pt"}, headers=headers
    )
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert count(s) == 0


def test_a_permanent_bounce_suppresses_the_address(db):
    client, s = setup(db)
    response = client.post(
        "/events/email",
        json={"type": "bounce", "email": " Ana@A.pt ", "permanent": True, "detail": "mailbox gone"},
        headers=AUTH,
    )
    assert response.status_code == 200 and response.json() == {"status": "suppressed"}
    record = s.suppressions.list()[0]
    assert (record.email, record.reason, record.source) == ("ana@a.pt", "hard_bounce", "event")
    assert record.detail == "mailbox gone"


def test_a_complaint_suppresses_the_address(db):
    client, s = setup(db)
    response = client.post(
        "/events/email", json={"type": "complaint", "email": "ana@a.pt"}, headers=AUTH
    )
    assert response.status_code == 200 and response.json() == {"status": "suppressed"}
    assert s.suppressions.list()[0].reason == "complaint"


def test_a_temporary_bounce_is_ignored(db):
    client, s = setup(db)
    response = client.post(
        "/events/email",
        json={"type": "bounce", "email": "ana@a.pt", "permanent": False},
        headers=AUTH,
    )
    assert response.status_code == 200 and response.json() == {"status": "ignored"}
    assert count(s) == 0


@pytest.mark.parametrize(
    "payload",
    [
        {"type": "bounce", "email": "ana@a.pt"},  # sem permanent
        {"type": "bounce", "email": "ana@a.pt", "permanent": "true"},  # não booleano
        {"type": "open", "email": "ana@a.pt"},
        {"email": "ana@a.pt"},
        {"type": "complaint", "email": "sem-arroba"},
        {"type": "complaint", "email": 5},
        {"type": "complaint"},
        {"type": "complaint", "email": "ana@a.pt", "detail": 5},
        [1, 2],
        "texto",
    ],
)
def test_invalid_events_are_rejected_and_write_nothing(db, payload):
    client, s = setup(db)
    response = client.post("/events/email", json=payload, headers=AUTH)
    assert response.status_code == 422
    assert count(s) == 0


def test_a_body_that_is_not_json_is_rejected(db):
    client, s = setup(db)
    response = client.post("/events/email", content=b"{nao json", headers=AUTH)
    assert response.status_code == 422 and count(s) == 0


def test_an_oversized_body_is_rejected(db):
    client, s = setup(db)
    response = client.post("/events/email", content=b"x" * 9000, headers=AUTH)
    assert response.status_code == 413 and count(s) == 0


def test_repeating_an_event_does_not_duplicate_the_address(db):
    client, s = setup(db)
    event = {"type": "bounce", "email": "ana@a.pt", "permanent": True}
    assert client.post("/events/email", json=event, headers=AUTH).status_code == 200
    assert client.post("/events/email", json=event, headers=AUTH).status_code == 200
    assert count(s) == 1


def test_an_event_promotes_a_manual_suppression_so_it_cannot_be_removed(db):
    client, s = setup(db)
    s.suppressions.add("ana@a.pt", "manual", "admin", "")
    client.post("/events/email", json={"type": "complaint", "email": "ana@a.pt"}, headers=AUTH)
    response = client.post("/suppressions/remove", data={"email": "ana@a.pt"})
    assert response.status_code == 422
    assert s.suppressions.is_suppressed("ana@a.pt")


# --- configuração do token ---


def test_the_token_comes_from_the_environment_and_a_short_one_is_an_error():
    assert load_events_token({}) is None
    assert load_events_token({"MINI_MARKETO_EVENTS_TOKEN": "   "}) is None
    assert load_events_token({"MINI_MARKETO_EVENTS_TOKEN": f" {TOKEN} "}) == TOKEN
    with pytest.raises(ConfigError, match="MINI_MARKETO_EVENTS_TOKEN"):
        load_events_token({"MINI_MARKETO_EVENTS_TOKEN": "curto"})


def test_the_app_refuses_to_start_with_a_short_token():
    with pytest.raises(ConfigError):
        create_app(service=object(), events_token="curto")
