"""Explicit HTTP access matrix. This module alone does not protect routes.

Roles must come from a validated server-side session/account, never request input.
Public routes retain their own validation (links, consent or event bearer token).
"""

import re


_READ = (
    r"/", r"/contacts", r"/contacts/[0-9]+/consent",
    r"/segments", r"/segments/[0-9]+", r"/campaigns",
    r"/campaigns/[0-9]+", r"/suppressions",
    r"/docs", r"/docs/oauth2-redirect", r"/redoc", r"/openapi\.json",
)
_WRITE = (
    r"/contacts", r"/contacts/[0-9]+/emails",
    r"/contacts/[0-9]+/emails/primary", r"/contacts/[0-9]+/emails/remove",
    r"/contacts/[0-9]+/consent", r"/companies", r"/segments",
    r"/segments/[0-9]+/edit", r"/segments/[0-9]+/delete",
    r"/campaigns", r"/campaigns/[0-9]+/enqueue",
    r"/suppressions", r"/suppressions/remove",
)
_PUBLIC_GET = (r"/subscribe", r"/subscribe/received",
               r"/subscribe/confirm/[^/]+", r"/unsubscribe/[^/]+", r"/static/.+")
_PUBLIC_POST = (r"/subscribe", r"/subscribe/confirm/[^/]+", r"/unsubscribe/[^/]+",
                r"/events/email")


def _matches(path, patterns):
    return any(re.fullmatch(pattern, path) is not None for pattern in patterns)


def classify_route(method, path):
    """Return public/login/read/edit/write/logout, or None for deny by default.

    Expects a decoded URL path, without query string. No normalization or second
    URL decoding is performed here. HEAD follows GET policy, not handler support.
    """
    if not isinstance(method, str) or not isinstance(path, str) or not 1 <= len(path) <= 4096:
        return None
    if any(ord(c) <= 32 or ord(c) == 127 for c in path) or any(c in path for c in ("%", "\\", "?", "#")):
        return None
    if any(segment in (".", "..") for segment in path.split("/")):
        return None
    method = method.upper()
    if path == "/login" and method in ("GET", "HEAD", "POST"):
        return "login"
    if path == "/logout" and method == "POST":
        return "logout"
    if method in ("GET", "HEAD"):
        if _matches(path, _PUBLIC_GET):
            return "public"
        if re.fullmatch(r"/segments/[0-9]+/edit", path):
            return "edit"
        if _matches(path, _READ):
            return "read"
    elif method == "POST":
        if _matches(path, _PUBLIC_POST):
            return "public"
        if _matches(path, _WRITE):
            return "write"
    return None


def role_allows(kind, role):
    """Role decision only: does not validate session, CSRF or route-owned tokens."""
    if kind in ("public", "login"):
        return True
    if role not in ("admin", "viewer"):
        return False
    if kind in ("read", "logout"):
        return True
    return role == "admin" and kind in ("edit", "write")


def csrf_purpose(method, kind):
    """Callers must still deny an unclassified/unsupported route."""
    if not isinstance(method, str) or method.upper() != "POST":
        return None
    return {"login": "login", "write": "admin", "logout": "logout"}.get(kind)
