from dataclasses import dataclass

from ..domain.campaign import utc_now
from ..domain.contact import DuplicateEmailError
from ..domain.subscription import SubscriptionRequest, is_expired


@dataclass(frozen=True)
class ConfirmationView:
    request: SubscriptionRequest
    confirmed: bool


class ConfirmationService:
    """Confirma um pedido de subscrição (double opt-in): só aqui nasce o contacto e o consentimento.

    `contacts` é um ContactService e `consents` um ConsentService, do mesmo backend e da mesma
    ligação. Qualquer falha — token inválido, pedido inexistente, expirado ou endereço suprimido —
    dá None, para a página não dizer o que falhou. Repetir a confirmação não altera nada.
    """

    def __init__(self, requests, suppressions, contacts, consents, links, now=utc_now):
        if suppressions is None:
            raise ValueError("A confirmação exige a lista de supressão.")
        if links is None:
            raise ValueError("A confirmação exige as ligações de confirmação.")
        self.requests = requests
        self.suppressions = suppressions
        self.contacts = contacts
        self.consents = consents
        self.links = links
        self.now = now

    def preview(self, token: str) -> ConfirmationView | None:
        """Para a página de confirmação (GET): não grava nada."""
        request = self._usable(token)
        if request is None:
            return None
        return ConfirmationView(request, request.confirmed_at is not None)

    def confirm(self, token: str) -> ConfirmationView | None:
        request = self._usable(token)
        if request is None:
            return None
        if request.confirmed_at is not None:
            return ConfirmationView(request, True)
        contact = self.contacts.find_by_email(request.email)
        if contact is None:
            try:
                contact = self.contacts.create_contact(
                    request.first_name, request.last_name, request.email
                )
            except DuplicateEmailError:  # outro pedido criou-o entretanto
                contact = self.contacts.find_by_email(request.email)
            if contact is None:
                return None
        # Um contacto já existente mantém o nome. Só se concede o que ainda não está concedido:
        # confirmar duas vezes não enche o histórico, e quem tinha retirado volta a ficar
        # subscrito porque confirmou outra vez.
        for purpose in request.purposes:
            if self.consents.status(contact.id, purpose) != "granted":
                self.consents.record(contact.id, purpose, "granted", "form")
        self.requests.mark_confirmed(request.id, self.now())
        return ConfirmationView(self.requests.get(request.id), True)

    def _usable(self, token: str) -> SubscriptionRequest | None:
        request_id = self.links.request_id_from(token)
        if request_id is None:
            return None
        request = self.requests.get(request_id)
        if request is None:
            return None
        if request.confirmed_at is not None:
            return request
        if is_expired(request.created_at, self.now()):
            return None
        if self.suppressions.is_suppressed(request.email):
            return None
        return request
