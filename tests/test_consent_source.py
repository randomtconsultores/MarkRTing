import pytest


def insert_event(handle, contact_id, source):
    p = handle.p
    handle.execute(
        "INSERT INTO consent_events (contact_id, purpose, decision, source, email) "
        f"VALUES ({p}, {p}, {p}, {p}, {p})",
        (contact_id, "newsletter", "withdrawn", source, "ana@a.pt"),
    )


def rows(handle):
    cursor = handle.execute(
        "SELECT id, contact_id, purpose, decision, source, email, recorded_at "
        "FROM consent_events ORDER BY id"
    )
    return [tuple(r.values()) if isinstance(r, dict) else tuple(r) for r in cursor.fetchall()]


def test_unsubscribe_is_a_valid_source_and_unknown_ones_are_not(db):
    ana = db.service().create_contact("Ana", "Silva", "ana@a.pt")
    consents = db.consent_service()
    record = consents.record(ana.id, "newsletter", "withdrawn", "unsubscribe")
    assert record.source == "unsubscribe"
    insert_event(db, ana.id, "unsubscribe")
    with pytest.raises(db.integrity_error):
        insert_event(db, ana.id, "robo")


def test_migration_v6_keeps_every_event_and_widens_the_source(db):
    ana = db.service().create_contact("Ana", "Silva", "ana@a.pt")
    rui = db.service().create_contact("Rui", "Costa", "rui@a.pt")
    consents = db.consent_service()
    consents.record(ana.id, "newsletter", "granted", "admin")
    consents.record(ana.id, "promotional", "granted", "form")
    consents.record(ana.id, "newsletter", "withdrawn", "admin")
    consents.record(rui.id, "newsletter", "granted", "form")
    before = rows(db)
    assert len(before) == 4

    # Volta ao esquema anterior: restrição de origem só com form e admin.
    if db.kind == "sqlite":
        db.conn.executescript("DROP TABLE consent_events;\n" + db.module.CONSENT_V4)
        for row in before:
            db.execute(
                "INSERT INTO consent_events "
                "(id, contact_id, purpose, decision, source, email, recorded_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                row,
            )
    else:
        db.execute("ALTER TABLE consent_events DROP CONSTRAINT consent_events_source_check")
        db.execute(
            "ALTER TABLE consent_events ADD CONSTRAINT consent_events_source_check "
            "CHECK (source IN ('form', 'admin'))"
        )
    assert rows(db) == before
    with pytest.raises(db.integrity_error):
        insert_event(db, ana.id, "unsubscribe")

    # Recuar para a v5 desfaz também a v7 (supressões), que a reabertura volta a criar.
    db.execute("DROP TABLE suppressions")
    db.set_version(5)
    again = db.reopen()
    try:
        assert again.version() == again.module.SCHEMA_VERSION
        assert rows(again) == before
        insert_event(again, ana.id, "unsubscribe")
        after = rows(again)
        assert len(after) == 5 and after[-1][0] > before[-1][0]
        assert after[-1][4] == "unsubscribe"
        with pytest.raises(again.integrity_error):
            insert_event(again, ana.id, "robo")
        status = again.consent_service().status(ana.id, "newsletter")
        assert status == "withdrawn"
    finally:
        again.close()


def test_the_index_for_the_current_state_exists_after_the_migration(db):
    if db.kind == "sqlite":
        names = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type = 'index'").fetchall()}
    else:
        names = {r["indexname"] for r in db.execute("SELECT indexname FROM pg_indexes WHERE tablename = 'consent_events'").fetchall()}
    assert "ix_consent_contact_purpose" in names
