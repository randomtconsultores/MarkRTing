from fastapi.testclient import TestClient

from mini_marketo.main import create_app


def client(tmp_path):
    return TestClient(create_app(tmp_path / "web.db"))


def test_page_loads(tmp_path):
    assert client(tmp_path).get("/contacts").status_code == 200


def test_create_and_list(tmp_path):
    c = client(tmp_path)
    r = c.post("/contacts", data={"first_name": "Ana", "last_name": "S", "email": "A@B.pt"})
    assert r.status_code == 200 and "a@b.pt" in r.text


def test_duplicate_shows_error(tmp_path):
    c = client(tmp_path)
    d = {"first_name": "Ana", "last_name": "S", "email": "a@b.pt"}
    c.post("/contacts", data=d)
    r = c.post("/contacts", data=d)
    assert r.status_code == 422 and "Já existe" in r.text
