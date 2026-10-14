from ..domain.consent import (
    DECISIONS, PURPOSES, SOURCES, STATUS_LABELS, ConsentRecord, ContactNotFoundError,
    current_status, is_eligible, latest_record, validate_decision,
)


class ConsentService:
    def __init__(self, consents, contacts):
        self.consents = consents
        self.contacts = contacts

    def record(self, contact_id: int, purpose: str, decision: str, source: str) -> ConsentRecord:
        # Valida tudo antes de qualquer escrita.
        purpose, decision, source = validate_decision(purpose, decision, source)
        contact = self.contacts.get_contact(contact_id)
        if contact is None or not contact.email:
            raise ContactNotFoundError("Contacto inexistente.")
        return self.consents.add(contact.id, purpose, decision, source, contact.email)

    def history(self, contact_id: int) -> list[ConsentRecord]:
        """Mais recente primeiro."""
        return sorted(self.consents.list_for_contact(contact_id), key=lambda r: r.id, reverse=True)

    def status(self, contact_id: int, purpose: str) -> str:
        return current_status(self.consents.list_for_contact(contact_id), purpose)

    def is_eligible(self, contact_id: int, purpose: str) -> bool:
        return is_eligible(self.consents.list_for_contact(contact_id), purpose)

    def view(self, contact_id: int) -> dict:
        contact = self.contacts.get_contact(contact_id)
        if contact is None:
            raise ContactNotFoundError("Contacto inexistente.")
        records = self.consents.list_for_contact(contact_id)
        rows = [
            {"purpose": key, "label": label,
             "status": current_status(records, key),
             "status_label": STATUS_LABELS[current_status(records, key)],
             "latest": latest_record(records, key)}
            for key, label in PURPOSES.items()
        ]
        return {"contact": contact, "rows": rows,
                "history": sorted(records, key=lambda r: r.id, reverse=True),
                "purposes": PURPOSES, "decisions": DECISIONS, "sources": SOURCES,
                "status_labels": STATUS_LABELS}
