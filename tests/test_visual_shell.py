from admin_app_support import TestClient, create_app



def test_contacts_use_the_new_shell_and_accessible_table(service):
    client = TestClient(create_app(service=service, events_token="", privacy_url=""))
    response = client.get("/contacts")
    assert response.status_code == 200
    assert 'href="/static/identity.css"' in response.text
    assert 'class="mm-app"' in response.text
    assert 'id="main-content"' in response.text
    assert 'href="#main-content"' in response.text
    assert '<h1>Contactos</h1>' in response.text
    assert 'href="/contacts" aria-current="page"' in response.text
    assert 'scope="col"' in response.text
    assert 'action="/contacts"' in response.text
    assert 'action="/companies"' in response.text
    assert client.get("/static/identity.css").status_code == 200
