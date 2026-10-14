from ..domain.campaign import SKIP_NO_CONSENT, Campaign, utc_now
from ..domain.consent import is_eligible
from ..domain.contact import ValidationError


class CampaignService:
    """Campanhas e caixa de saída. O envio não acontece aqui: este serviço só enfileira.

    `campaigns` é o repositório de campanhas, `segments` um SegmentService e `consents` o
    repositório de consentimento, todos do mesmo backend e da mesma ligação.
    """

    def __init__(self, campaigns, segments, consents, now=utc_now):
        self.campaigns = campaigns
        self.segments = segments
        self.consents = consents
        self.now = now

    def create_campaign(
        self,
        name: str,
        subject: str,
        body_text: str,
        body_html: str,
        purpose: str,
        segment_id: int,
    ) -> Campaign:
        segment = self.segments.get_segment(segment_id)  # ValidationError se não existir
        campaign = Campaign(
            None, name, subject, body_text, body_html, purpose, segment.id, segment.name
        )
        return self.campaigns.add_campaign(campaign)

    def get_campaign(self, campaign_id: int) -> Campaign:
        campaign = self.campaigns.get_campaign(campaign_id)
        if campaign is None:
            raise ValidationError("A campanha não existe.")
        return campaign

    def list_campaigns(self) -> list[Campaign]:
        return self.campaigns.list_campaigns()

    def list_segments(self):
        return self.segments.list_segments()

    def enqueue(self, campaign_id: int) -> dict[str, int]:
        """Cria uma mensagem por membro do segmento: pendente se o contacto tem consentimento
        concedido para a finalidade, ignorada (com motivo) se não tem. O consentimento volta a
        verificar-se no momento do envio."""
        campaign = self.get_campaign(campaign_id)
        if campaign.status != "draft":
            raise ValidationError("A campanha já foi enfileirada.")
        if campaign.segment_id is None:
            raise ValidationError("O segmento desta campanha já não existe.")
        members = self.segments.members(campaign.segment_id)
        by_contact: dict[int, list] = {}
        for record in self.consents.list_all():
            by_contact.setdefault(record.contact_id, []).append(record)
        pending, skipped = [], []
        for member in members:
            if not member.email:
                continue
            if is_eligible(by_contact.get(member.id, ()), campaign.purpose):
                pending.append((member.id, member.email))
            else:
                skipped.append((member.id, member.email, SKIP_NO_CONSENT))
        self.campaigns.queue_campaign(campaign.id, pending, skipped, self.now())
        return self.campaigns.counts(campaign.id)

    def summary(self, campaign_id: int) -> dict:
        campaign = self.get_campaign(campaign_id)
        return {
            "campaign": campaign,
            "counts": self.campaigns.counts(campaign.id),
            "problems": self.campaigns.problem_messages(campaign.id),
        }
