from admin_app_support import TestClient, create_app



def make_client(service):
    c = TestClient(create_app(service=service))
    c.post("/contacts", data={"first_name": "Ana", "last_name": "S", "email": "a@x.pt"})
    return c


def test_add_email_shows_both(service):
    c = make_client(service)
    r = c.post("/contacts/1/emails", data={"email": "B@x.pt"})
    assert r.status_code == 200 and "a@x.pt" in r.text and "b@x.pt" in r.text


def test_add_duplicate_email_shows_error(service):
    c = make_client(service)
    r = c.post("/contacts/1/emails", data={"email": " A@x.pt"})
    assert r.status_code == 422 and "Já existe" in r.text


def test_set_primary_via_web(service):
    c = make_client(service)
    c.post("/contacts/1/emails", data={"email": "b@x.pt"})
    r = c.post("/contacts/1/emails/primary", data={"email": "b@x.pt"})
    assert r.status_code == 200
    assert 'name="email" value="a@x.pt"' in r.text
    assert 'name="email" value="b@x.pt"' not in r.text


def test_remove_primary_via_web_rejected(service):
    c = make_client(service)
    c.post("/contacts/1/emails", data={"email": "b@x.pt"})
    r = c.post("/contacts/1/emails/remove", data={"email": "a@x.pt"})
    assert r.status_code == 422 and "principal" in r.text


def test_unknown_contact_via_web(service):
    c = make_client(service)
    r = c.post("/contacts/99/emails", data={"email": "b@x.pt"})
    assert r.status_code == 422
