from dataclasses import dataclass

from ..domain.consent import ContactNotFoundError, current_status


@dataclass(frozen=True)
class UnsubscribeView:
    purpose: str
    masked_email: str
    already_withdrawn: bool


def mask_email(email: str) -> str:
    """Mostra o suficiente para a pessoa reconhecer o endereço, sem o expor a quem tenha a ligação."""
    local, at, domain = (email or "").partition("@")
    if not at or not local or not domain:
        return "***"
    return f"{local[0]}***@{domain}"


class UnsubscribeService:
    """Cancelamento de subscrição a partir da ligação de uma mensagem enviada.

    `campaigns` é o repositório de campanhas, `consents` o de consentimento (mesma ligação) e
    `links` o `UnsubscribeLinks` (ou None se não estiver configurado: nesse caso nenhuma
    ligação é aceite). Token inválido, mensagem inexistente e serviço não configurado dão todos
    None, para a resposta não dizer o que falhou.
    """

    def __init__(self, campaigns, consents, links):
        self.campaigns = campaigns
        self.consents = consents
        self.links = links

    def _find(self, token: str):
        if self.links is None:
            return None
        message_id = self.links.message_id_from(token)
        if message_id is None:
            return None
        message = self.campaigns.get_message(message_id)
        if message is None:
            return None
        campaign = self.campaigns.get_campaign(message.campaign_id)
        if campaign is None:
            return None
        return message, campaign

    def _view(self, message, campaign, already: bool) -> UnsubscribeView:
        return UnsubscribeView(campaign.purpose, mask_email(message.email), already)

    def _already_withdrawn(self, message, campaign) -> bool:
        records = self.consents.list_for_contact(message.contact_id)
        return current_status(records, campaign.purpose) == "withdrawn"

    def preview(self, token: str) -> UnsubscribeView | None:
        """O que a página de confirmação mostra. Não altera nada."""
        found = self._find(token)
        if found is None:
            return None
        message, campaign = found
        return self._view(message, campaign, self._already_withdrawn(message, campaign))

    def unsubscribe(self, token: str) -> UnsubscribeView | None:
        """Retira o consentimento da finalidade da campanha, com origem «unsubscribe» e o email
        da mensagem. Idempotente: se já está retirado, não acrescenta outro evento."""
        found = self._find(token)
        if found is None:
            return None
        message, campaign = found
        if not self._already_withdrawn(message, campaign):
            try:
                self.consents.add(
                    message.contact_id, campaign.purpose, "withdrawn", "unsubscribe", message.email
                )
            except ContactNotFoundError:
                return None
        return self._view(message, campaign, True)
