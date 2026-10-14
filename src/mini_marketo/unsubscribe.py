import base64
import hashlib
import hmac
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from .mailer import ConfigError

MIN_SECRET_LENGTH = 32
LOOPBACK_HOSTS = ("localhost", "127.0.0.1", "::1")
# id da mensagem, ponto, e 16 bytes de assinatura em base64 sem preenchimento (22 caracteres).
_TOKEN = re.compile(r"^([0-9]{1,12})\.([A-Za-z0-9_-]{22})$")


def _sign(secret: str, message_id: int) -> str:
    digest = hmac.new(
        secret.encode("utf-8"), f"unsubscribe:{message_id}".encode(), hashlib.sha256
    ).digest()[:16]
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


@dataclass(frozen=True)
class UnsubscribeLinks:
    """Ligações de cancelamento de subscrição: um token por mensagem, sem guardar nada.

    O token é o id da mensagem mais uma assinatura HMAC-SHA256 (128 bits) com `secret`: sem a
    chave não se forja uma ligação. `previous_secrets` são chaves antigas, só para verificar:
    assim, rodar a chave não inutiliza as ligações de emails já enviados.
    """

    base_url: str
    secret: str = field(repr=False)
    previous_secrets: tuple[str, ...] = field(default=(), repr=False)

    def __post_init__(self):
        parts = urlsplit(self.base_url)
        if parts.scheme not in ("https", "http") or not parts.hostname:
            raise ConfigError("MINI_MARKETO_PUBLIC_URL tem de ser um URL absoluto (https).")
        if parts.scheme == "http" and parts.hostname not in LOOPBACK_HOSTS:
            raise ConfigError("MINI_MARKETO_PUBLIC_URL tem de ser https (http só para local).")
        if parts.query or parts.fragment:
            raise ConfigError("MINI_MARKETO_PUBLIC_URL não pode ter parâmetros nem fragmento.")
        for key in (self.secret, *self.previous_secrets):
            if len(key) < MIN_SECRET_LENGTH:
                raise ConfigError(
                    f"As chaves MINI_MARKETO_SECRET_KEY têm de ter pelo menos {MIN_SECRET_LENGTH} caracteres."
                )

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "UnsubscribeLinks":
        env = os.environ if env is None else env
        previous = tuple(
            part.strip()
            for part in env.get("MINI_MARKETO_SECRET_KEY_PREVIOUS", "").split(",")
            if part.strip()
        )
        return cls(
            base_url=env.get("MINI_MARKETO_PUBLIC_URL", "").strip(),
            secret=env.get("MINI_MARKETO_SECRET_KEY", "").strip(),
            previous_secrets=previous,
        )

    def token_for(self, message_id: int) -> str:
        return f"{message_id}.{_sign(self.secret, message_id)}"

    def url_for(self, message_id: int) -> str:
        return f"{self.base_url.rstrip('/')}/unsubscribe/{self.token_for(message_id)}"

    def message_id_from(self, token: str) -> int | None:
        """O id da mensagem se o token é autêntico; None em qualquer outro caso."""
        match = _TOKEN.match(token or "")
        if match is None:
            return None
        message_id = int(match.group(1))
        if message_id < 1:
            return None
        signature = match.group(2)
        for key in (self.secret, *self.previous_secrets):
            if hmac.compare_digest(_sign(key, message_id), signature):
                return message_id
        return None
