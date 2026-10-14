from datetime import datetime

from mini_marketo.domain.segment import Criterion
from mini_marketo.services.campaign_service import CampaignService

T0 = datetime(2026, 1, 15, 12, 0, 0)
EVERYONE = [Criterion("created_at", "after", "2000-01-01")]


def campaign_repository(db):
    if db.kind == "sqlite":
        from mini_marketo.repositories.campaign_repository import CampaignRepository

        return CampaignRepository(db.conn)
    from mini_marketo.repositories.postgres_campaign_repository import (
        PostgresCampaignRepository,
    )

    return PostgresCampaignRepository(db.conn)


class Setup:
    """Ana e Rui com newsletter concedida, Eva sem registo, e um segmento com todos."""

    def __init__(self, db, now=T0):
        self.db = db
        self.contacts = db.service()
        self.segments = db.segment_service()
        self.consents = db.consent_service()
        self.repo = campaign_repository(db)
        self.service = CampaignService(
            self.repo, self.segments, self.segments.consents, now=lambda: now
        )
        self.ana = self.contacts.create_contact("Ana", "Silva", "ana@a.pt")
        self.rui = self.contacts.create_contact("Rui", "Costa", "rui@a.pt")
        self.eva = self.contacts.create_contact("Eva", "Lopes", "eva@a.pt")
        self.consents.record(self.ana.id, "newsletter", "granted", "admin")
        self.consents.record(self.rui.id, "newsletter", "granted", "admin")
        self.segment = self.segments.create_segment("Todos", "", "all", EVERYONE)

    def campaign(self, **overrides):
        data = dict(
            name="Outubro",
            subject="Olá {first_name}",
            body_text="Olá {first_name} {last_name}",
            body_html="",
            purpose="newsletter",
            segment_id=self.segment.id,
        )
        data.update(overrides)
        return self.service.create_campaign(**data)

    def message_for(self, campaign_id, contact):
        return next(m for m in self.repo.list_messages(campaign_id) if m.contact_id == contact.id)
