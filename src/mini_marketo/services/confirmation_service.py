from dataclasses import dataclass

from ..domain.campaign import utc_now
from ..domain.subscription import SubscriptionRequest, is_expired
from ..repositories.confirmation_repository import ConfirmationRepository


@dataclass(frozen=True)
class ConfirmationView:
    request: SubscriptionRequest
    confirmed: bool


class ConfirmationService:
    """Valida a ligação; delega as escritas compostas numa operação transacional única.

    A assinatura do construtor mantém-se para as fábricas existentes. Todos os repositórios
    têm de partilhar a mesma ligação; falha explicitamente se a configuração violar esse contrato.
    """

    def __init__(self, requests, suppressions, contacts, consents, links, now=utc_now):
        if suppressions is None:
            raise ValueError("A confirmação exige a lista de supressão.")
        if links is None:
            raise ValueError("A confirmação exige as ligações de confirmação.")
        conn = requests.conn
        if any(other is not conn for other in (
            contacts.repo.conn, consents.consents.conn, consents.contacts.conn,
            suppressions.suppressions.conn,
        )):
            raise ValueError("A confirmação exige a mesma ligação para todos os repositórios.")
        self.requests = requests
        self.suppressions = suppressions
        self.links = links
        self.now = now
        self.writer = ConfirmationRepository(requests)

    def preview(self, token: str) -> ConfirmationView | None:
        request = self._usable(token)
        if request is None:
            return None
        return ConfirmationView(request, request.confirmed_at is not None)

    def confirm(self, token: str) -> ConfirmationView | None:
        request_id = self.links.request_id_from(token)
        if request_id is None:
            return None
        request = self.writer.confirm(request_id, self.now())
        return ConfirmationView(request, True) if request is not None else None

    def _usable(self, token: str) -> SubscriptionRequest | None:
        request_id = self.links.request_id_from(token)
        if request_id is None:
            return None
        request = self.requests.get(request_id)
        if request is None or request.confirmed_at is not None:
            return request
        if is_expired(request.created_at, self.now()):
            return None
        if self.suppressions.is_suppressed(request.email):
            return None
        return request
