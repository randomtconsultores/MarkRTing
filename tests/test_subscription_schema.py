import pytest


def insert_request(db, email="ana@a.pt", first="Ana", last="Silva", lang="pt"):
    p = db.p
    db.execute(
        "INSERT INTO subscription_requests (email, first_name, last_name, lang) "
        f"VALUES ({p}, {p}, {p}, {p})",
        (email, first, last, lang),
    )
    return db.scalar("SELECT max(id) FROM subscription_requests")


def insert_purpose(db, request_id, purpose):
    p = db.p
    db.execute(
        f"INSERT INTO subscription_request_purposes (request_id, purpose) VALUES ({p}, {p})",
        (request_id, purpose),
    )


def test_a_new_request_starts_pending_and_unconfirmed(db):
    request_id = insert_request(db)
    p = db.p
    row = db.execute(
        "SELECT delivery_status, attempts, last_error, skip_reason, claimed_at, sent_at, "
        f"confirmed_at FROM subscription_requests WHERE id = {p}",
        (request_id,),
    ).fetchone()
    values = tuple(row.values()) if isinstance(row, dict) else tuple(row)
    assert values == ("pending", 0, "", "", None, None, None)


def test_the_schema_enforces_its_own_rules(db):
    request_id = insert_request(db)
    insert_purpose(db, request_id, "newsletter")
    p = db.p
    for args in (
        ("A@a.pt", "Ana", "Silva", "pt"),  # email não normalizado
        ("", "Ana", "Silva", "pt"),
        ("a@a.pt", " ", "Silva", "pt"),
        ("a@a.pt", "Ana", " ", "pt"),
        ("a@a.pt", "Ana", "Silva", "p"),  # idioma curto demais
        ("a@a.pt", "Ana", "Silva", "portugues-pt"),  # idioma longo demais
    ):
        with pytest.raises(db.integrity_error):
            insert_request(db, *args)
    for args in (
        (request_id, "outra"),  # finalidade desconhecida
        (request_id, "newsletter"),  # repetida no mesmo pedido
        (999999, "newsletter"),  # pedido inexistente
    ):
        with pytest.raises(db.integrity_error):
            insert_purpose(db, *args)
    for sql, value in (
        (f"UPDATE subscription_requests SET delivery_status = {p} WHERE id = {p}", "enviado"),
        (f"UPDATE subscription_requests SET attempts = {p} WHERE id = {p}", -1),
    ):
        with pytest.raises(db.integrity_error):
            db.execute(sql, (value, request_id))
    assert db.scalar("SELECT count(*) FROM subscription_requests") == 1
    assert db.scalar("SELECT count(*) FROM subscription_request_purposes") == 1


def test_deleting_a_request_deletes_its_purposes(db):
    request_id = insert_request(db)
    insert_purpose(db, request_id, "newsletter")
    insert_purpose(db, request_id, "promotional")
    other = insert_request(db, "rui@a.pt")
    insert_purpose(db, other, "newsletter")
    db.execute(f"DELETE FROM subscription_requests WHERE id = {db.p}", (request_id,))
    assert db.scalar("SELECT count(*) FROM subscription_request_purposes") == 1


def test_migration_v8_creates_the_tables_and_keeps_existing_data(db):
    db.service().create_contact("Ana", "Silva", "ana@a.pt")
    db.set_version(7)  # a fixture apaga também as tabelas das migrações seguintes
    again = db.reopen()
    try:
        assert again.module.SCHEMA_VERSION >= 8
        assert again.version() == again.module.SCHEMA_VERSION
        assert again.scalar("SELECT count(*) FROM contact_emails") == 1
        request_id = insert_request(again)
        insert_purpose(again, request_id, "promotional")
        assert again.scalar("SELECT count(*) FROM subscription_request_purposes") == 1
    finally:
        again.close()
