import sqlite3
from datetime import datetime

from ..domain.contact import Company, Contact, DuplicateEmailError, ValidationError


class ContactRepository:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def add_company(self, name: str, website: str = "") -> Company:
        try:
            with self.conn:
                cur = self.conn.execute(
                    "INSERT INTO companies (name, website) VALUES (?, ?)", (name, website)
                )
        except sqlite3.IntegrityError as exc:
            raise ValidationError("Já existe uma empresa com esse nome.") from exc
        return Company(cur.lastrowid, name, website)

    def list_companies(self) -> list[Company]:
        rows = self.conn.execute(
            "SELECT id, name, website FROM companies ORDER BY name COLLATE NOCASE"
        ).fetchall()
        return [Company(r["id"], r["name"], r["website"]) for r in rows]

    def company_exists(self, company_id: int) -> bool:
        return self.conn.execute(
            "SELECT 1 FROM companies WHERE id = ?", (company_id,)
        ).fetchone() is not None

    def email_exists(self, email: str) -> bool:
        return self.conn.execute(
            "SELECT 1 FROM contacts WHERE email = ?", (email,)
        ).fetchone() is not None

    def add_contact(self, email, first_name, last_name, company_id=None) -> Contact:
        try:
            with self.conn:
                cur = self.conn.execute(
                    "INSERT INTO contacts (email, first_name, last_name, company_id) "
                    "VALUES (?, ?, ?, ?)",
                    (email, first_name, last_name, company_id),
                )
        except sqlite3.IntegrityError as exc:
            if "contacts.email" in str(exc):
                raise DuplicateEmailError("Já existe um contacto com esse email.") from exc
            raise ValidationError("Violação de integridade dos dados.") from exc
        return self.get_contact(cur.lastrowid)

    def get_contact(self, contact_id: int) -> Contact | None:
        row = self.conn.execute(self._SELECT + " WHERE c.id = ?", (contact_id,)).fetchone()
        return self._to_contact(row) if row else None

    def list_contacts(self) -> list[Contact]:
        rows = self.conn.execute(self._SELECT + " ORDER BY c.created_at DESC, c.id DESC").fetchall()
        return [self._to_contact(r) for r in rows]

    _SELECT = (
        "SELECT c.id, c.email, c.first_name, c.last_name, c.company_id, c.created_at, "
        "co.name AS company_name FROM contacts c "
        "LEFT JOIN companies co ON co.id = c.company_id"
    )

    @staticmethod
    def _to_contact(r) -> Contact:
        return Contact(
            id=r["id"], email=r["email"], first_name=r["first_name"],
            last_name=r["last_name"], company_id=r["company_id"],
            company_name=r["company_name"],
            created_at=datetime.fromisoformat(r["created_at"]),
        )
