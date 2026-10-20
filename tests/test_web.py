from admin_app_support import TestClient, create_app



def test_page_loads(service):
    assert TestClient(create_app(service=service)).get("/contacts").status_code == 200


def test_create_and_list(service):
    c = TestClient(create_app(service=service))
    r = c.post("/contacts", data={"first_name": "Ana", "last_name": "S", "email": "A@B.pt"})
    assert r.status_code == 200 and "a@b.pt" in r.text


def test_duplicate_shows_error(service):
    c = TestClient(create_app(service=service))
    d = {"first_name": "Ana", "last_name": "S", "email": "a@b.pt"}
    c.post("/contacts", data=d)
    r = c.post("/contacts", data=d)
    assert r.status_code == 422 and "Já existe" in r.text
