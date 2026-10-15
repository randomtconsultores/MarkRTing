from ..domain.campaign import utc_now
from ..domain.subscription import (
    DEFAULT_LANG, MAX_REQUESTS_PER_DAY, REQUEST_WINDOW, validate_subscription,
)


class SubscriptionService:
    """Só regista pedidos. Não cria contactos, não concede consentimento e não envia email.

    O resultado interno não deve ser exposto pela página pública: todos os pedidos válidos
    recebem a mesma resposta, incluindo os suprimidos e os limitados.
    """

    def __init__(self, requests, suppressions, languages, now=utc_now):
        if suppressions is None:
            raise ValueError("A subscrição exige a lista de supressão.")
        self.languages = tuple(languages)
        if DEFAULT_LANG not in self.languages:
            raise ValueError("Falta o idioma por omissão.")
        self.requests = requests
        self.suppressions = suppressions
        self.now = now

    def request(self, email, first_name, last_name, purposes, lang=""):
        email, first_name, last_name, purposes, lang = validate_subscription(
            email, first_name, last_name, purposes, lang, self.languages
        )
        if self.suppressions.is_suppressed(email):
            return "suppressed"
        now = self.now()
        request_id = self.requests.add_limited(
            email, first_name, last_name, lang, purposes, now,
            now - REQUEST_WINDOW, MAX_REQUESTS_PER_DAY,
        )
        return "queued" if request_id is not None else "rate_limited"
