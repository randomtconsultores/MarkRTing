"""Fail-closed ASGI guard for classified administrative routes."""


from email.message import Message
import logging
import re
import time
from urllib.parse import parse_qsl


from starlette.concurrency import run_in_threadpool
from starlette.datastructures import Headers
from starlette.responses import JSONResponse


from mini_marketo.access_policy import classify_route, csrf_purpose, role_allows
from mini_marketo.services.session_service import SessionService
from mini_marketo.web_security import CsrfTokens, WebSecurityConfig, private_response, same_origin



_MAX_TIME = 2**63 - 1
_BAD_PERCENT = re.compile(r"%(?![0-9a-fA-F]{2})")
_LOGGER = logging.getLogger(__name__)



class AdminAccessGuard:
    """Protect classified business routes without consuming handler request bodies."""


    def __init__(
        self,
        app,
        *,
        repo_factory,
        web_config,
        max_body_bytes,
        max_form_fields,
        clock=None,
    ):
        if not callable(repo_factory):
            raise ValueError("A fabrica de repositorios deve ser invocavel.")
        if not isinstance(web_config, WebSecurityConfig):
            raise ValueError("Configuracao web invalida.")
        if type(max_body_bytes) is not int or max_body_bytes < 1:
            raise ValueError("max_body_bytes deve ser um inteiro positivo.")
        if type(max_form_fields) is not int or max_form_fields < 1:
            raise ValueError("max_form_fields deve ser um inteiro positivo.")
        if clock is not None and not callable(clock):
            raise ValueError("O relogio deve ser invocavel.")
        self.app = app
        self.repo_factory = repo_factory
        self.web_config = web_config
        self.max_body_bytes = max_body_bytes
        self.max_form_fields = max_form_fields
        self.clock = clock if clock is not None else lambda: int(time.time())
        self.csrf = CsrfTokens(web_config)


    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return


        kind = classify_route(scope.get("method"), scope.get("path"))
        if kind == "public":
            await self.app(scope, receive, send)
            return
        if kind in ("login", "logout"):
            # The access router owns origin, CSRF, form and session checks for these endpoints.
            await self.app(scope, receive, send)
            return
        if kind is None:
            await self._failure(scope, send, 404)
            return


        headers = Headers(scope=scope)
        timestamp = self._now()
        if timestamp is None:
            await self._failure(scope, send, 503)
            return


        session_token = self._cookie(headers, self.web_config.session_cookie)
        if session_token is None:
            await self._failure(scope, send, 401)
            return


        csrf_kind = csrf_purpose(scope.get("method"), kind)
        replay_receive = receive
        if csrf_kind is not None:
            if not self._valid_origin(headers):
                await self._failure(scope, send, 403)
                return
            try:
                body, messages = await self._read_body(receive)
            except _BodyTooLarge:
                await self._failure(scope, send, 413)
                return
            except _InvalidBody:
                await self._failure(scope, send, 400)
                return
            csrf_token = self._csrf_from_body(headers, body)
            if csrf_token is None or not self.csrf.verify(
                csrf_token, session_token, csrf_kind, now=timestamp
            ):
                await self._failure(scope, send, 403)
                return
            replay_receive = self._replay(messages, receive)


        try:
            role = await run_in_threadpool(self._resolve_role, session_token, timestamp)
        except Exception:
            _LOGGER.error("Falha ao validar a sessao administrativa.")
            await self._failure(scope, send, 503)
            return
        if role is None or not role_allows(kind, role):
            await self._failure(scope, send, 401 if role is None else 403)
            return


        await self.app(scope, replay_receive, send)


    def _now(self):
        try:
            value = self.clock()
        except Exception:
            _LOGGER.error("Falha ao obter o relogio do guard administrativo.")
            return None
        if type(value) is not int or not 0 <= value <= _MAX_TIME:
            _LOGGER.error("O relogio do guard administrativo devolveu uma data invalida.")
            return None
        return value


    def _resolve_role(self, token, timestamp):
        with self.repo_factory() as repo:
            sessions = SessionService(
                repo,
                absolute_seconds=self.web_config.absolute_seconds,
                idle_seconds=self.web_config.idle_seconds,
            )
            session = sessions.resolve(token, now=timestamp)
            if session is None:
                return None
            user = repo.get_user(session.user_id)
            if (
                user is None
                or not user.is_active
                or user.role not in ("admin", "viewer")
                or user.credential_version != session.credential_version
            ):
                return None
            return user.role


    @staticmethod
    def _cookie(headers, name):
        matches = []
        for header in headers.getlist("cookie"):
            for item in header.split(";"):
                key, separator, value = item.strip().partition("=")
                if key == name:
                    if not separator:
                        return None
                    matches.append(value)
        return matches[0] if len(matches) == 1 and matches[0] else None


    def _valid_origin(self, headers):
        origins = headers.getlist("origin")
        referers = headers.getlist("referer")
        if len(origins) > 1 or len(referers) > 1:
            return False
        return same_origin(
            self.web_config,
            origin=origins[0] if origins else None,
            referer=referers[0] if referers else None,
        )


    async def _read_body(self, receive):
        messages = []
        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                raise _InvalidBody
            if message["type"] != "http.request":
                raise _InvalidBody
            chunk = message.get("body", b"")
            if not isinstance(chunk, bytes):
                raise _InvalidBody
            if len(body) + len(chunk) > self.max_body_bytes:
                raise _BodyTooLarge
            messages.append(message)
            body.extend(chunk)
            if not message.get("more_body", False):
                return bytes(body), messages


    @staticmethod
    def _replay(messages, receive):
        index = 0


        async def replay():
            nonlocal index
            if index < len(messages):
                message = messages[index]
                index += 1
                return message
            return await receive()


        return replay


    def _csrf_from_body(self, headers, body):
        content_types = headers.getlist("content-type")
        if len(content_types) != 1:
            return None
        content_type = Message()
        content_type["content-type"] = content_types[0]
        if content_type.get_content_type().lower() != "application/x-www-form-urlencoded":
            return None
        charset = content_type.get_content_charset()
        if charset is not None and charset.lower() != "utf-8":
            return None
        try:
            encoded = body.decode("ascii")
            if _BAD_PERCENT.search(encoded):
                return None
            fields = parse_qsl(
                encoded,
                keep_blank_values=True,
                strict_parsing=False,
                encoding="utf-8",
                errors="strict",
                max_num_fields=self.max_form_fields,
            )
        except (UnicodeDecodeError, ValueError):
            return None
        tokens = [value for name, value in fields if name == "csrf_token"]
        return tokens[0] if len(tokens) == 1 else None


    @staticmethod
    async def _failure(scope, send, status):
        response = JSONResponse({"detail": "Pedido administrativo recusado."}, status_code=status)
        private_response(response)
        await response(scope, _empty_receive, send)



async def _empty_receive():
    return {"type": "http.disconnect"}



class _BodyTooLarge(Exception):
    pass



class _InvalidBody(Exception):
    pass
