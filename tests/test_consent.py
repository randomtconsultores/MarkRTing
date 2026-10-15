import threading

import pytest

from mini_marketo.domain.consent import (
    ConsentRecord, ContactNotFoundError, current_status, is_eligible,
)
from mini_marketo.domain.contact import ValidationError


@pytest.fixture
def ctx(db):
    contact = db.service().create_contact("Ana", "Silva", "ana@exemplo.pt", None)
    return db, db.consent_service(), contact


def test_sem_registo_nao_e_consentimento(ctx):
    _, consents, c = ctx
    assert consents.status(c.id, "newsletter") == "none"
    assert consents.is_eligible(c.id, "newsletter") is False


def test_concessao_inicial(ctx):
    _, consents, c = ctx
    r = consents.record(c.id, "newsletter", "granted", "admin")
    assert (r.contact_id, r.purpose, r.decision, r.source) == (c.id, "newsletter", "granted", "admin")
    assert r.email == "ana@exemplo.pt"
    assert r.recorded_at.tzinfo is None
    assert consents.is_eligible(c.id, "newsletter") is True


def test_retirada_prevalece_e_historico_fica(ctx):
    _, consents, c = ctx
    first = consents.record(c.id, "newsletter", "granted", "form")
    consents.record(c.id, "newsletter", "withdrawn", "admin")
    assert consents.status(c.id, "newsletter") == "withdrawn"
    assert consents.is_eligible(c.id, "newsletter") is False
    assert first.id in [r.id for r in consents.history(c.id)]
    assert len(consents.history(c.id)) == 2


def test_finalidades_independentes(ctx):
    _, consents, c = ctx
    consents.record(c.id, "newsletter", "granted", "admin")
    assert consents.status(c.id, "promotional") == "none"
    assert consents.is_eligible(c.id, "promotional") is False


def test_decisoes_consecutivas_e_repetidas(ctx):
    _, consents, c = ctx
    for decision in ("granted", "granted", "withdrawn", "granted"):
        consents.record(c.id, "promotional", decision, "admin")
    assert len(consents.history(c.id)) == 4
    assert consents.status(c.id, "promotional") == "granted"


def test_contacto_inexistente(ctx):
    db, consents, _ = ctx
    with pytest.raises(ContactNotFoundError):
        consents.record(999999, "newsletter", "granted", "admin")
    assert db.scalar("SELECT count(*) FROM consent_events") == 0


@pytest.mark.parametrize("args", [
    ("", "granted", "admin"), ("outra", "granted", "admin"),
    ("newsletter", "", "admin"), ("newsletter", "talvez", "admin"),
    ("newsletter", "granted", ""), ("newsletter", "granted", "robo"),
])
def test_dados_invalidos_nao_gravam(ctx, args):
    db, consents, c = ctx
    with pytest.raises(ValidationError):
        consents.record(c.id, *args)
    assert db.scalar("SELECT count(*) FROM consent_events") == 0


def test_associacao_ao_contacto_e_email(ctx):
    db, consents, c = ctx
    outro = db.service().create_contact("Rui", "Costa", "rui@exemplo.pt", None)
    consents.record(c.id, "newsletter", "granted", "admin")
    assert consents.status(outro.id, "newsletter") == "none"
    assert [r.email for r in consents.history(c.id)] == ["ana@exemplo.pt"]


def test_email_principal_muda_e_historico_mantem_o_antigo(ctx):
    db, consents, c = ctx
    consents.record(c.id, "newsletter", "granted", "admin")
    svc = db.service()
    svc.add_email(c.id, "ana2@exemplo.pt")
    svc.set_primary_email(c.id, "ana2@exemplo.pt")
    consents.record(c.id, "newsletter", "withdrawn", "admin")
    assert [r.email for r in consents.history(c.id)] == ["ana2@exemplo.pt", "ana@exemplo.pt"]


def test_base_de_dados_rejeita_valores_invalidos(ctx):
    db, _, c = ctx
    p = db.p
    with pytest.raises(db.integrity_error):
        db.execute(
            "INSERT INTO consent_events (contact_id, purpose, decision, source, email) "
            f"VALUES ({p}, {p}, {p}, {p}, {p})",
            (c.id, "inventada", "granted", "admin", "ana@exemplo.pt"),
        )


def test_migracao_v4_preserva_dados(ctx):
    db, consents, c = ctx
    # Desfaz também a v5 (campanhas), que depende de contactos e segmentos mas não da v4,
    # e a v7 (supressões), que não depende de nada mas tem de voltar a ser criada.
    db.execute("DROP TABLE email_messages")
    db.execute("DROP TABLE campaigns")
    db.execute("DROP TABLE suppressions")
    db.execute("DROP TABLE consent_events")
    db.set_version(3)
    again = db.reopen()
    try:
        assert again.version() == again.module.SCHEMA_VERSION
        assert [x.id for x in again.service().list_contacts()] == [c.id]
        again.consent_service().record(c.id, "newsletter", "granted", "admin")
    finally:
        again.close()


def test_dominio_puro_ordem_por_id():
    from datetime import datetime
    now = datetime(2026, 1, 1)
    h = [ConsentRecord(2, 1, "newsletter", "withdrawn", "admin", "a@b.pt", now),
         ConsentRecord(1, 1, "newsletter", "granted", "form", "a@b.pt", now)]
    assert current_status(h, "newsletter") == "withdrawn"
    assert is_eligible(h, "newsletter") is False
    assert current_status([], "newsletter") == "none"


def test_concorrencia_nao_perde_historico(ctx):
    db, _, c = ctx
    n, errors = 12, []

    def work(i):
        handle = db.reopen()
        try:
            handle.consent_service().record(
                c.id, "newsletter", "granted" if i % 2 else "withdrawn", "admin"
            )
        except Exception as exc:
            errors.append(exc)
        finally:
            handle.close()

    threads = [threading.Thread(target=work, args=(i,)) for i in range(n)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert errors == []
    history = db.consent_service().history(c.id)
    assert len(history) == n
    assert len({r.id for r in history}) == n
    assert db.consent_service().status(c.id, "newsletter") == history[0].decision
