"""Administrative login/logout router; deliberately not mounted by main.py yet."""

import hmac
from pathlib import Path
import secrets
import time

from fastapi import APIRouter, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from mini_marketo.login_form import LoginFormError, read_login_form
from mini_marketo.services.authentication_service import AuthenticationService
from mini_marketo.services.login_attempt_limiter import LoginAttemptConfig, LoginAttemptLimiter
from mini_marketo.services.session_service import SessionService
from mini_marketo.web_security import (
    CsrfTokens, WebSecurityConfig, clear_access_cookies, private_response,
    safe_redirect, same_origin, set_login_cookie, set_session_cookie,
)


def create_access_router(repo_factory, *, web_config, login_config, max_form_bytes, clock=None):
    """Each synchronous operation opens/closes its repository inside the worker.

    repo_factory must return a synchronous context manager, not a shared connection.
    Mount only together with the global access guard in the application factory.
    """
    if not callable(repo_factory):
        raise ValueError("A fabrica de repositorios deve ser invocavel.")
    if not isinstance(web_config, WebSecurityConfig) or not isinstance(login_config, LoginAttemptConfig):
        raise ValueError("Configuracao de acesso invalida.")
    if hmac.compare_digest(web_config.csrf_key, login_config.hmac_key):
        raise ValueError("As chaves CSRF e de limitacao devem ser independentes.")
    if type(max_form_bytes) is not int or max_form_bytes < 1:
        raise ValueError("max_form_bytes deve ser um inteiro positivo.")
    if clock is None:
        clock = lambda: int(time.time())
    elif not callable(clock):
        raise ValueError("O relogio deve ser invocavel.")
    router = APIRouter()
    templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
    csrf = CsrfTokens(web_config)

    def now():
        value = clock()
        if type(value) is not int or not 0 <= value <= 2**63 - 1:
            raise ValueError("Data invalida.")
        duration = max(web_config.absolute_seconds, web_config.csrf_seconds, login_config.window_seconds)
        if value + duration > 2**63 - 1:
            raise ValueError("Expiracao invalida.")
        return value

    def failure(status, headers=None):
        response = JSONResponse({"detail": "Pedido de acesso recusado."}, status_code=status, headers=headers)
        private_response(response)
        return response

    def valid_origin(request):
        if len(request.headers.getlist("origin")) > 1 or len(request.headers.getlist("referer")) > 1:
            return False
        return same_origin(web_config, origin=request.headers.get("origin"),
                           referer=request.headers.get("referer"))

    def cookies(request):
        names = (web_config.login_cookie, web_config.session_cookie)
        for name in names:
            count = sum(part.partition("=")[0].strip() == name
                        for header in request.headers.getlist("cookie") for part in header.split(";"))
            if count > 1:
                return None
        return tuple(request.cookies.get(name, "") for name in names)

    async def form(request, *, allowed, required, count):
        try:
            fields = await read_login_form(request.receive, request.headers.get("content-type"),
                                           max_bytes=max_form_bytes, max_fields=count)
        except LoginFormError as error:
            return None, failure(error.status_code)
        if not fields.keys() <= allowed or not required <= fields.keys():
            return None, failure(400)
        return fields, None

    def login_page(request, target, timestamp, *, error=False, status=200):
        nonce = secrets.token_urlsafe(32)
        token = csrf.issue(nonce, "login", now=timestamp)
        response = templates.TemplateResponse(
            request, "admin_login.html",
            {"csrf_token": token, "next": safe_redirect(target), "error": error},
            status_code=status,
        )
        set_login_cookie(response, web_config, nonce)
        if request.method == "HEAD":
            response.body = b""
        return response

    def authenticate(username, password, address, old_token, timestamp):
        with repo_factory() as repo:
            decision = LoginAttemptLimiter(repo, config=login_config).reserve(username, address, now=timestamp)
            if not decision.allowed:
                return decision, None
            auth = AuthenticationService(repo, absolute_seconds=web_config.absolute_seconds,
                                         idle_seconds=web_config.idle_seconds)
            issued = auth.authenticate(username, password, now=timestamp)
            if issued is not None and old_token:
                auth.sessions.revoke(old_token)
            return decision, issued

    def logout(token, timestamp):
        with repo_factory() as repo:
            sessions = SessionService(repo, absolute_seconds=web_config.absolute_seconds,
                                      idle_seconds=web_config.idle_seconds)
            session = sessions.resolve(token, now=timestamp)
            if session is None:
                return False
            user = repo.get_user(session.user_id)
            if (user is None or not user.is_active or user.role not in ("admin", "viewer")
                    or user.credential_version != session.credential_version):
                return False
            return sessions.revoke(token)

    @router.api_route("/login", methods=["GET", "HEAD"], include_in_schema=False)
    async def show_login(request: Request):
        try:
            return login_page(request, request.query_params.get("next", "/"), now())
        except Exception:
            return failure(503)

    @router.post("/login", include_in_schema=False)
    async def submit_login(request: Request):
        if not valid_origin(request):
            return failure(403)
        supplied = cookies(request)
        if supplied is None:
            return failure(403)
        fields, error = await form(request, allowed={"username", "password", "csrf_token", "next"},
                                   required={"username", "password", "csrf_token"}, count=4)
        if error is not None:
            return error
        try:
            timestamp = now()
            if not csrf.verify(fields["csrf_token"], supplied[0], "login", now=timestamp):
                return failure(403)
            if request.client is None:
                return failure(503)
            decision, issued = await run_in_threadpool(
                authenticate, fields["username"], fields["password"], request.client.host, supplied[1], timestamp,
            )
            if not decision.allowed:
                return failure(429, {"Retry-After": str(decision.retry_after)})
            target = safe_redirect(fields.get("next", "/"))
            if issued is None:
                return login_page(request, target, timestamp, error=True, status=401)
            response = RedirectResponse(target, status_code=303)
            clear_access_cookies(response, web_config)
            set_session_cookie(response, web_config, issued.token, expires_at=issued.expires_at, now=timestamp)
            return response
        except Exception:
            return failure(503)

    @router.post("/logout", include_in_schema=False)
    async def submit_logout(request: Request):
        if not valid_origin(request):
            return failure(403)
        supplied = cookies(request)
        if supplied is None:
            return failure(403)
        fields, error = await form(request, allowed={"csrf_token"}, required={"csrf_token"}, count=1)
        if error is not None:
            return error
        try:
            timestamp = now()
            if not csrf.verify(fields["csrf_token"], supplied[1], "logout", now=timestamp):
                return failure(403)
            revoked = await run_in_threadpool(logout, supplied[1], timestamp)
            response = RedirectResponse("/login", status_code=303) if revoked else failure(401)
            clear_access_cookies(response, web_config)
            return response
        except Exception:
            return failure(503)

    return router
