from ..domain.contact import (
    Company, Contact, DuplicateEmailError, ValidationError, clean_name, validate_email,
)
from ..repositories.contact_repository import ContactRepository


class ContactService:
    def __init__(self, repo: ContactRepository):
        self.repo = repo

    def create_company(self, name: str, website: str = "") -> Company:
        name = clean_name(name, "nome da empresa")
        website = (website or "").strip()
        if len(website) > 200:
            raise ValidationError("Website demasiado longo.")
        return self.repo.add_company(name, website)

    def create_contact(self, first_name, last_name, email, company_id: int | None = None) -> Contact:
        first_name = clean_name(first_name, "nome")
        last_name = clean_name(last_name, "apelido")
        email = validate_email(email)
        if company_id is not None and not self.repo.company_exists(company_id):
            raise ValidationError("A empresa indicada não existe.")
        if self.repo.email_exists(email):
            raise DuplicateEmailError("Já existe um contacto com esse email.")
        return self.repo.add_contact(email, first_name, last_name, company_id)

    def list_contacts(self) -> list[Contact]:
        return self.repo.list_contacts()

    def list_companies(self) -> list[Company]:
        return self.repo.list_companies()
