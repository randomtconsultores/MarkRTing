"""Escrita composta da confirmação, sem chamar repositórios que fazem commit por operação."""
import sqlite3
from datetime import datetime, timezone

from ..domain.subscription import is_expired


class ConfirmationRepository:
    def __init__(self, requests):
        self.requests = requests
        self.conn = requests.conn
        self.sqlite = isinstance(self.conn, sqlite3.Connection)

    def execute(self, sql, params=()):
        if not self.sqlite:
            sql = sql.replace("?", "%s")
            params = tuple(
                v.replace(tzinfo=timezone.utc) if isinstance(v, datetime) else v
                for v in params
            )
        else:
            params = tuple(
                v.isoformat(sep=" ", timespec="microseconds") if isinstance(v, datetime) else v
                for v in params
            )
        return self.conn.execute(sql, params)

    def confirm(self, request_id, now):
        if self.sqlite:
            if self.conn.in_transaction:
                raise RuntimeError("A confirmação exige uma ligação sem transação ativa.")
            try:
                self.conn.execute("BEGIN IMMEDIATE")
                result = self._confirm(request_id, now)
                self.conn.commit()
                return result
            except BaseException:
                self.conn.rollback()
                raise
        with self.conn.transaction():
            # Serializa confirmações do mesmo pedido, mesmo antes de ler o seu estado.
            self.execute("SELECT id FROM subscription_requests WHERE id = ? FOR UPDATE", (request_id,))
            return self._confirm(request_id, now)

    def _confirm(self, request_id, now):
        request = self.requests.get(request_id)
        if request is None or request.confirmed_at is not None:
            return request
        if is_expired(request.created_at, now):
            return None
        if self.execute("SELECT 1 FROM suppressions WHERE email = ?", (request.email,)).fetchone():
            return None
        if not request.purposes:
            raise ValueError("Pedido sem finalidades: confirmação abortada.")
        if not self.sqlite:
            # Pedidos diferentes para o mesmo endereço também não duplicam o contacto.
            self.execute("SELECT pg_advisory_xact_lock(7305002, hashtext(?))", (request.email,))
        row = self.execute(
            "SELECT contact_id FROM contact_emails WHERE email = ?", (request.email,)
        ).fetchone()
        if row:
            contact_id = row["contact_id"]
        else:
            cur = self.execute(
                "INSERT INTO contacts (first_name, last_name) VALUES (?, ?) RETURNING id",
                (request.first_name, request.last_name),
            )
            created_id = cur.fetchone()["id"]
            primary = "1" if self.sqlite else "TRUE"
            inserted = self.execute(
                "INSERT INTO contact_emails (contact_id, email, is_primary) "
                f"VALUES (?, ?, {primary}) ON CONFLICT (email) DO NOTHING",
                (created_id, request.email),
            )
            if inserted.rowcount == 1:
                contact_id = created_id
            else:
                # Outro fluxo criou o endereço. Remove só o contacto vazio criado nesta transação.
                self.execute("DELETE FROM contacts WHERE id = ?", (created_id,))
                row = self.execute(
                    "SELECT contact_id FROM contact_emails WHERE email = ?", (request.email,)
                ).fetchone()
                if row is None:
                    raise RuntimeError("O endereço mudou durante a confirmação; repetir o pedido.")
                contact_id = row["contact_id"]
        if not self.sqlite:
            self.execute("SELECT id FROM contacts WHERE id = ? FOR UPDATE", (contact_id,))
        for purpose in request.purposes:
            latest = self.execute(
                "SELECT decision FROM consent_events WHERE contact_id = ? AND purpose = ? "
                "ORDER BY id DESC LIMIT 1", (contact_id, purpose),
            ).fetchone()
            if latest is None or latest["decision"] != "granted":
                self.execute(
                    "INSERT INTO consent_events "
                    "(contact_id, purpose, decision, source, email, recorded_at) "
                    "VALUES (?, ?, 'granted', 'form', ?, ?)",
                    (contact_id, purpose, request.email, now),
                )
        self.execute(
            "UPDATE subscription_requests SET confirmed_at = ? WHERE id = ? AND confirmed_at IS NULL",
            (now, request_id),
        )
        return self.requests.get(request_id)
