from fastapi.testclient import TestClient

from mini_marketo.main import create_app


def make_client(tmp_path):
    c = TestClient(create_app(tmp_path / "web.db"))
    c.post("/contacts", data={"first_name": "Ana", "last_name": "S", "email": "a@x.pt"})
    return c


def test_add_email_shows_both(tmp_path):
    c = make_client(tmp_path)
    r = c.post("/contacts/1/emails", data={"email": "B@x.pt"})
    assert r.status_code == 200 and "a@x.pt" in r.text and "b@x.pt" in r.text


def test_add_duplicate_email_shows_error(tmp_path):
    c = make_client(tmp_path)
    r = c.post("/contacts/1/emails", data={"email": " A@x.pt"})
    assert r.status_code == 422 and "Já existe" in r.text


def test_set_primary_via_web(tmp_path):
    c = make_client(tmp_path)
    c.post("/contacts/1/emails", data={"email": "b@x.pt"})
    r = c.post("/contacts/1/emails/primary", data={"email": "b@x.pt"})
    assert r.status_code == 200
    assert 'name="email" value="a@x.pt"' in r.text
    assert 'name="email" value="b@x.pt"' not in r.text


def test_remove_primary_via_web_rejected(tmp_path):
    c = make_client(tmp_path)
    c.post("/contacts/1/emails", data={"email": "b@x.pt"})
    r = c.post("/contacts/1/emails/remove", data={"email": "a@x.pt"})
    assert r.status_code == 422 and "principal" in r.text


def test_unknown_contact_via_web(tmp_path):
    c = make_client(tmp_path)
    r = c.post("/contacts/99/emails", data={"email": "b@x.pt"})
    assert r.status_code == 422
