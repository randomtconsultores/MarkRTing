import base64
import hashlib
import hmac
import re
from dataclasses import dataclass

from .unsubscribe import UnsubscribeLinks

# id do pedido, ponto, e 16 bytes de assinatura em base64 sem preenchimento (22 caracteres).
_TOKEN = re.compile(r"^([0-9]{1,12})\.([A-Za-z0-9_-]{22})$")


def _sign(secret: str, request_id: int) -> str:
    # O prefixo separa os domínios: um token de cancelamento nunca valida aqui, nem o inverso.
    digest = hmac.new(
        secret.encode("utf-8"), f"subscribe:{request_id}".encode(), hashlib.sha256
    ).digest()[:16]
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


@dataclass(frozen=True)
class SubscriptionLinks:
    """Ligações de confirmação da subscrição: um token por pedido, sem guardar nada.

    Usa o URL público e as chaves (atual e anteriores) do cancelamento, com outro prefixo na
    assinatura. A validade de 48 horas não está no token: verifica-se no servidor.
    """

    base: UnsubscribeLinks

    def token_for(self, request_id: int) -> str:
        return f"{request_id}.{_sign(self.base.secret, request_id)}"

    def url_for(self, request_id: int) -> str:
        return f"{self.base.base_url.rstrip('/')}/subscribe/confirm/{self.token_for(request_id)}"

    def request_id_from(self, token: str) -> int | None:
        """O id do pedido se o token é autêntico; None em qualquer outro caso."""
        match = _TOKEN.match(token or "")
        if match is None:
            return None
        request_id = int(match.group(1))
        if request_id < 1:
            return None
        signature = match.group(2)
        for key in (self.base.secret, *self.base.previous_secrets):
            if hmac.compare_digest(_sign(key, request_id), signature):
                return request_id
        return None
