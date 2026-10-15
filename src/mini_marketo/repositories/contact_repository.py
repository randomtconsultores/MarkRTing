import sqlite3
from datetime import datetime

from ..domain.contact import Company, Contact, DuplicateEmailError, ValidationError


def _translate(exc: sqlite3.IntegrityError) -> ValidationError:
    if "contact_emails.email" in str(exc):
        return DuplicateEmailError("Já existe um contacto com esse email.")
    return ValidationError("Violação de integridade dos dados.")


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
            "SELECT 1 FROM contact_emails WHERE email = ?", (email,)
        ).fetchone() is not None

    def get_contact_by_email(self, email: str) -> Contact | None:
        """O contacto a que o email pertence (principal ou não); `email` já normalizado."""
        row = self.conn.execute(
            "SELECT contact_id FROM contact_emails WHERE email = ?", (email,)
        ).fetchone()
        return self.get_contact(row["contact_id"]) if row else None

    def add_contact(self, email, first_name, last_name, company_id=None) -> Contact:
        try:
            with self.conn:
                cur = self.conn.execute(
                    "INSERT INTO contacts (first_name, last_name, company_id) VALUES (?, ?, ?)",
                    (first_name, last_name, company_id),
                )
                contact_id = cur.lastrowid
                self.conn.execute(
                    "INSERT INTO contact_emails (contact_id, email, is_primary) VALUES (?, ?, 1)",
                    (contact_id, email),
                )
        except sqlite3.IntegrityError as exc:
            raise _translate(exc) from exc
        return self.get_contact(contact_id)

    def add_email(self, contact_id: int, email: str) -> None:
        try:
            with self.conn:
                self.conn.execute(
                    "INSERT INTO contact_emails (contact_id, email, is_primary) VALUES (?, ?, 0)",
                    (contact_id, email),
                )
        except sqlite3.IntegrityError as exc:
            raise _translate(exc) from exc

    def set_primary_email(self, contact_id: int, email: str) -> None:
        with self.conn:
            self.conn.execute(
                "UPDATE contact_emails SET is_primary = 0 WHERE contact_id = ?", (contact_id,)
            )
            cur = self.conn.execute(
                "UPDATE contact_emails SET is_primary = 1 WHERE contact_id = ? AND email = ?",
                (contact_id, email),
            )
            if cur.rowcount != 1:
                raise ValidationError("O email não pertence a este contacto.")

    def remove_email(self, contact_id: int, email: str) -> None:
        with self.conn:
            cur = self.conn.execute(
                "DELETE FROM contact_emails WHERE contact_id = ? AND email = ? AND is_primary = 0",
                (contact_id, email),
            )
            if cur.rowcount != 1:
                raise ValidationError("Não foi possível remover o email.")

    def get_contact(self, contact_id: int) -> Contact | None:
        row = self.conn.execute(self._SELECT + " WHERE c.id = ?", (contact_id,)).fetchone()
        if row is None:
            return None
        emails = self._load_emails(" WHERE contact_id = ?", (contact_id,))
        return self._to_contact(row, emails)

    def list_contacts(self) -> list[Contact]:
        rows = self.conn.execute(self._SELECT + " ORDER BY c.created_at DESC, c.id DESC").fetchall()
        emails = self._load_emails()
        return [self._to_contact(r, emails) for r in rows]

    _SELECT = (
        "SELECT c.id, c.first_name, c.last_name, c.company_id, c.created_at, "
        "co.name AS company_name FROM contacts c "
        "LEFT JOIN companies co ON co.id = c.company_id"
    )

    def _load_emails(self, where: str = "", params: tuple = ()) -> dict[int, list[str]]:
        rows = self.conn.execute(
            "SELECT contact_id, email FROM contact_emails" + where + " ORDER BY is_primary DESC, id",
            params,
        ).fetchall()
        out: dict[int, list[str]] = {}
        for r in rows:
            out.setdefault(r["contact_id"], []).append(r["email"])
        return out

    @staticmethod
    def _to_contact(r, emails: dict[int, list[str]]) -> Contact:
        return Contact(
            id=r["id"], first_name=r["first_name"], last_name=r["last_name"],
            emails=tuple(emails.get(r["id"], [])),
            company_id=r["company_id"], company_name=r["company_name"],
            created_at=datetime.fromisoformat(r["created_at"]),
        )
