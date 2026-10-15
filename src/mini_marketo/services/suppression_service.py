from ..domain.contact import normalize_email
from ..domain.suppression import (
    Suppression, SuppressionNotFoundError, SuppressionNotRemovableError, is_removable,
    validate_suppression,
)


class SuppressionService:
    def __init__(self, suppressions):
        self.suppressions = suppressions

    def add(self, email: str, reason: str, source: str, detail: str = "") -> Suppression | None:
        """Idempotente. Devolve o estado final do endereço (None só se for removido entretanto)."""
        email, reason, source, detail = validate_suppression(email, reason, source, detail)
        self.suppressions.add(email, reason, source, detail)
        return self.suppressions.get(email)

    def is_suppressed(self, email: str) -> bool:
        email = normalize_email(email)
        return bool(email) and self.suppressions.get(email) is not None

    def suppressed_emails(self) -> set[str]:
        return self.suppressions.emails()

    def list(self) -> list[Suppression]:
        return self.suppressions.list_all()

    def remove(self, email: str) -> None:
        email = normalize_email(email)
        current = self.suppressions.get(email)
        if current is None:
            raise SuppressionNotFoundError("Endereço não suprimido.")
        if not is_removable(current):
            raise SuppressionNotRemovableError(
                "Só as supressões manuais podem ser removidas; bounces e queixas são definitivos."
            )
        # A escrita volta a exigir o motivo manual: protege contra uma promoção concorrente.
        if not self.suppressions.delete_manual(email):
            raise SuppressionNotRemovableError("A supressão já não é manual e não pode ser removida.")
