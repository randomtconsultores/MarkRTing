import ast
from pathlib import Path

import pytest

from mini_marketo.access_policy import classify_route, csrf_purpose, role_allows


@pytest.mark.parametrize("path", ["/", "/contacts", "/contacts/3/consent", "/segments", "/segments/3", "/campaigns", "/campaigns/3", "/suppressions", "/docs", "/docs/oauth2-redirect", "/redoc", "/openapi.json"])
def test_reads_require_known_role(path):
    kind = classify_route("GET", path)
    assert kind == "read"
    assert role_allows(kind, "admin") and role_allows(kind, "viewer")
    assert not role_allows(kind, None) and not role_allows(kind, "unknown")
    assert classify_route("HEAD", path) == kind


@pytest.mark.parametrize("path", ["/contacts", "/contacts/3/emails", "/contacts/3/emails/primary", "/contacts/3/emails/remove", "/contacts/3/consent", "/companies", "/segments", "/segments/3/edit", "/segments/3/delete", "/campaigns", "/campaigns/3/enqueue", "/suppressions", "/suppressions/remove"])
def test_writes_admin_only_with_csrf_contract(path):
    kind = classify_route("POST", path)
    assert kind == "write" and role_allows(kind, "admin")
    assert not role_allows(kind, "viewer") and not role_allows(kind, None)
    assert csrf_purpose("POST", kind) == "admin"


def test_edit_form_is_admin_only():
    kind = classify_route("GET", "/segments/3/edit")
    assert kind == "edit" and role_allows(kind, "admin")
    assert not role_allows(kind, "viewer")


@pytest.mark.parametrize("method,path", [
    ("GET", "/subscribe"), ("POST", "/subscribe"),
    ("GET", "/subscribe/received"), ("GET", "/subscribe/confirm/test-token"),
    ("POST", "/subscribe/confirm/test-token"), ("GET", "/unsubscribe/test-token"),
    ("POST", "/unsubscribe/test-token"), ("POST", "/events/email"),
    ("GET", "/static/style.css"),
])
def test_public_routes_keep_their_own_mechanisms(method, path):
    kind = classify_route(method, path)
    assert kind == "public" and role_allows(kind, None)
    assert csrf_purpose(method, kind) is None


@pytest.mark.parametrize("method,path", [
    ("GET", "/new-admin-route"), ("POST", "/subscribe/new"),
    ("POST", "/subscribe/received"), ("GET", "/events/email"),
    ("GET", "/logout"), ("DELETE", "/contacts"), ("OPTIONS", "/contacts"),
    ("POST", "/static/style.css"), ("GET", "/docs/new"),
    ("GET", "/static/../contacts"), ("GET", "/subscribe/../contacts"),
    ("GET", "/subscribe/confirm/a/b"), ("GET", "/unsubscribe/"),
    ("GET", "/%63ontacts"), ("GET", "/contacts?x=1"),
    ("GET", "/contacts#x"), ("GET", "/\\contacts"),
    ("GET", "/contacts\r\n"), (None, "/contacts"), ("GET", None),
])
def test_unclassified_and_ambiguous_routes_denied(method, path):
    assert classify_route(method, path) is None
    assert not role_allows(None, "admin")
    assert not role_allows(None, "viewer")


@pytest.mark.parametrize("role", [None, "admin", "viewer", "unknown"])
def test_login_and_logout_have_separate_csrf_purposes(role):
    assert classify_route("GET", "/login") == "login"
    assert classify_route("POST", "/login") == "login"
    assert role_allows("login", role)
    assert csrf_purpose("POST", "login") == "login"
    assert classify_route("POST", "/logout") == "logout"
    assert role_allows("logout", role) is (role in ("admin", "viewer"))
    assert csrf_purpose("POST", "logout") == "logout"
    assert csrf_purpose("GET", "read") is None


def test_all_declared_routes_have_an_explicit_policy():
    # Static analysis: no app startup or bypass of authentication is needed.
    source = Path(__file__).resolve().parents[1] / "src/mini_marketo/main.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    checked = 0
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            if not (isinstance(decorator, ast.Call) and isinstance(decorator.func, ast.Attribute)
                    and isinstance(decorator.func.value, ast.Name) and decorator.func.value.id == "app"):
                continue
            if decorator.func.attr == "api_route":
                pytest.fail("Review methods declared with app.api_route explicitly")
            if decorator.func.attr not in ("get", "post", "put", "patch", "delete", "head", "options"):
                continue
            assert decorator.args and isinstance(decorator.args[0], ast.Constant), "Review dynamic route policy"
            path = decorator.args[0].value
            for name in ("contact_id", "segment_id", "campaign_id"):
                path = path.replace("{" + name + "}", "123")
            path = path.replace("{token}", "test-token")
            kind = classify_route(decorator.func.attr.upper(), path)
            assert kind is not None, (decorator.func.attr, decorator.args[0].value)
            checked += 1
    assert checked >= 30, "Route inventory unexpectedly reduced; review the test"


@pytest.mark.parametrize("method", ["POST", "post", "Post"])
def test_post_csrf_purpose_is_case_independent(method):
    assert csrf_purpose(method, "write") == "admin"
    assert csrf_purpose(method, "login") == "login"
    assert csrf_purpose(method, "logout") == "logout"
