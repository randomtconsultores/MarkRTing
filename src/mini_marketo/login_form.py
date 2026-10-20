"""Bounded URL-encoded login/logout forms; not authentication or CSRF validation."""

import re
from urllib.parse import parse_qsl

_CONTENT_TYPE = re.compile(
    r'application/x-www-form-urlencoded(?:[ \t]*;[ \t]*charset=(?:utf-8|"utf-8"))?',
    re.IGNORECASE,
)


class LoginFormError(ValueError):
    def __init__(self, status_code):
        self.status_code = status_code
        super().__init__("Formulario administrativo invalido.")


async def read_login_form(receive, content_type, *, max_bytes, max_fields):
    """Read ASGI chunks with explicit bounds; preserve decoded values unchanged.

    Duplicate decoded names are rejected. Do not use for multi-value business
    forms. Callers still validate required fields, origin, CSRF and credentials.
    """
    if any(type(value) is not int or value < 1 for value in (max_bytes, max_fields)):
        raise ValueError("Os limites do formulario devem ser inteiros positivos.")
    if (not isinstance(content_type, str) or len(content_type) > 128
            or _CONTENT_TYPE.fullmatch(content_type.strip(" \t")) is None):
        raise LoginFormError(415)
    body = bytearray()
    while True:
        message = await receive()
        if message.get("type") != "http.request":
            raise LoginFormError(400)
        chunk = message.get("body", b"")
        more = message.get("more_body", False)
        if not isinstance(chunk, bytes) or type(more) is not bool:
            raise LoginFormError(400)
        if len(body) + len(chunk) > max_bytes:
            raise LoginFormError(413)
        body.extend(chunk)
        if not more:
            break
    try:
        encoded = body.decode("ascii")
        if re.search(r"%(?![0-9a-fA-F]{2})", encoded):
            raise ValueError()
        pairs = parse_qsl(encoded, keep_blank_values=True, strict_parsing=True,
                          encoding="utf-8", errors="strict", max_num_fields=max_fields,
                          separator="&")
        fields = dict(pairs)
        if len(fields) != len(pairs) or "" in fields:
            raise ValueError()
    except ValueError:
        raise LoginFormError(400) from None
    return fields
