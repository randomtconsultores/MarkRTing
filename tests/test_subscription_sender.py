from datetime import timedelta

import pytest
from test_delivery import LINKS, FakeMailer, clear_environment
from test_subscription import T0, make
from test_subscription_delivery import queue

from mini_marketo import send_worker
from mini_marketo.delivery import DeliveryLimits
from mini_marketo.domain.campaign import utc_now
from mini_marketo.mailer import ConfigError, SendError
from mini_marketo.subscription_delivery import (
    ConfirmationSender, confirmation_cap_from_env, load_privacy_url,
)
from mini_marketo.subscription_links import SubscriptionLinks

LINKS_OK = SubscriptionLinks(LINKS)
TTL = timedelta(hours=48)


def build(db, mailer, now=T0, cap=200, privacy="", **limits):
    _, repo, suppressions = make(db, now)
    sleeps = []
    sender = ConfirmationSender(
        repo, suppressions, mailer, DeliveryLimits(**limits), LINKS_OK, cap, privacy,
        now=lambda: now, sleep=sleeps.append,
    )
    return sender, repo, suppressions, sleeps


# --- sem base de dados ---


def test_the_daily_cap_comes_from_the_environment():
    name = "MINI_MARKETO_CONFIRMATION_DAILY_CAP"
    assert confirmation_cap_from_env({}) == 200
    assert confirmation_cap_from_env({name: " 5 "}) == 5
    assert confirmation_cap_from_env({name: "0"}) == 0
    for bad in ("muitos", "-1", "1.5"):
        with pytest.raises(ValueError, match=name):
            confirmation_cap_from_env({name: bad})


def test_the_privacy_url_is_optional_and_must_be_an_absolute_http_url():
    name = "MINI_MARKETO_PRIVACY_URL"
    assert load_privacy_url({}) == "" and load_privacy_url({name: "  "}) == ""
    assert load_privacy_url({name: " https://x.pt/privacidade "}) == "https://x.pt/privacidade"
    for bad in ("x.pt/privacidade", "ftp://x.pt", "javascript:alert(1)", "https://", "https://x.pt/a b"):
        with pytest.raises(ConfigError, match=name):
            load_privacy_url({name: bad})


def test_the_sender_cannot_be_built_without_what_it_needs():
    with pytest.raises(ValueError, match="supressão"):
        ConfirmationSender(None, None, None, DeliveryLimits(), LINKS_OK, 1)
    with pytest.raises(ValueError, match="confirmação"):
        ConfirmationSender(None, object(), None, DeliveryLimits(), None, 1)
    with pytest.raises(ValueError, match="teto"):
        ConfirmationSender(None, object(), None, DeliveryLimits(), LINKS_OK, -1)


# --- com base de dados, nos dois backends ---


def test_sends_the_confirmation_email_with_the_link(db):
    mailer = FakeMailer()
    sender, repo, _, sleeps = build(db, mailer)
    request_id = queue(repo, purposes=("newsletter", "promotional"))
    summary = sender.run_once()
    assert (summary.sent, summary.failed, summary.skipped, summary.retried) == (1, 0, 0, 0)
    to, subject, text, html = mailer.sent[0]
    assert (to, subject, html) == ("a@a.pt", "Confirme a sua subscrição", None)
    assert "Olá Ana," in text
    assert "Newsletter, Comunicações promocionais" in text
    assert LINKS_OK.url_for(request_id) in text
    assert "48 horas" in text
    assert "Política de privacidade" not in text
    sent = repo.get(request_id)
    assert (sent.delivery_status, sent.sent_at, sent.attempts) == ("sent", T0, 1)
    assert sleeps == [1.0]


def test_the_privacy_link_is_added_when_configured(db):
    mailer = FakeMailer()
    sender, repo, _, _ = build(db, mailer, privacy="https://x.pt/privacidade")
    queue(repo)
    sender.run_once()
    assert "Política de privacidade: https://x.pt/privacidade" in mailer.sent[0][2]


def test_a_second_run_sends_nothing_more(db):
    mailer = FakeMailer()
    sender, repo, _, _ = build(db, mailer)
    queue(repo)
    sender.run_once()
    assert sender.run_once().sent == 0 and len(mailer.sent) == 1


def test_a_suppressed_address_gets_no_confirmation(db):
    mailer = FakeMailer()
    sender, repo, suppressions, _ = build(db, mailer)
    request_id = queue(repo)
    suppressions.add("a@a.pt", "complaint", "event", "")
    summary = sender.run_once()
    assert (summary.sent, summary.skipped) == (0, 1) and mailer.sent == []
    skipped = repo.get(request_id)
    assert (skipped.delivery_status, skipped.skip_reason) == ("skipped", "suprimido")


def test_expired_and_confirmed_requests_are_not_sent(db):
    mailer = FakeMailer()
    sender, repo, _, _ = build(db, mailer)
    expired = queue(repo, "old@a.pt", T0 - TTL - timedelta(seconds=1))
    confirmed = queue(repo, "done@a.pt")
    repo.mark_confirmed(confirmed, T0)
    assert sender.run_once().sent == 0 and mailer.sent == []
    assert repo.get(expired).delivery_status == "pending"


def test_a_rejected_address_is_suppressed_and_a_policy_refusal_is_not(db):
    mailer = FakeMailer(fail={
        "gone@a.pt": [SendError("permanent", "550 5.1.1 user unknown", address_rejected=True)],
        "blocked@a.pt": [SendError("permanent", "550 5.7.1 blocked by policy")],
    })
    sender, repo, suppressions, _ = build(db, mailer)
    gone, blocked = queue(repo, "gone@a.pt"), queue(repo, "blocked@a.pt")
    summary = sender.run_once()
    assert (summary.failed, summary.suppressed, summary.sent) == (2, 1, 0)
    assert repo.get(gone).delivery_status == "failed"
    assert repo.get(blocked).delivery_status == "failed"
    assert suppressions.is_suppressed("gone@a.pt")
    assert not suppressions.is_suppressed("blocked@a.pt")


def test_a_temporary_failure_is_retried_after_the_backoff(db):
    mailer = FakeMailer(fail={"a@a.pt": [SendError("temporary", "451 later")]})
    first, repo, _, _ = build(db, mailer)
    request_id = queue(repo)
    assert first.run_once().retried == 1
    waiting = repo.get(request_id)
    assert (waiting.delivery_status, waiting.attempts, waiting.last_error) == ("pending", 1, "451 later")
    assert waiting.next_attempt_at == T0 + timedelta(seconds=60)
    early, _, _, _ = build(db, mailer, now=T0 + timedelta(seconds=30))
    assert early.run_once().sent == 0
    on_time, _, _, _ = build(db, mailer, now=T0 + timedelta(seconds=61))
    assert on_time.run_once().sent == 1
    assert repo.get(request_id).delivery_status == "sent"


def test_attempts_run_out(db):
    error = SendError("temporary", "451 later")
    mailer = FakeMailer(fail={"a@a.pt": [error, error]})
    first, repo, _, _ = build(db, mailer, max_attempts=2)
    request_id = queue(repo)
    first.run_once()
    second, _, _, _ = build(db, mailer, now=T0 + timedelta(hours=1), max_attempts=2)
    assert second.run_once().failed == 1
    final = repo.get(request_id)
    assert final.delivery_status == "failed" and "tentativas esgotadas" in final.last_error


def test_an_unexpected_exception_is_treated_as_temporary(db):
    mailer = FakeMailer(fail={"a@a.pt": [RuntimeError("estranho")]})
    sender, repo, _, _ = build(db, mailer)
    request_id = queue(repo)
    assert sender.run_once().retried == 1
    assert "RuntimeError" in repo.get(request_id).last_error


def test_a_fatal_error_stops_the_run_and_spends_no_attempt(db):
    fatal = SendError("fatal", "535 auth failed")
    mailer = FakeMailer(fail={"a@a.pt": [fatal]})
    sender, repo, _, _ = build(db, mailer)
    first, second = queue(repo, "a@a.pt"), queue(repo, "b@a.pt")
    summary = sender.run_once()
    assert summary.stopped.startswith("erro_fatal") and mailer.sent == []
    for request_id in (first, second):
        pending = repo.get(request_id)
        assert (pending.delivery_status, pending.attempts) == ("pending", 0)


def test_throttling_stops_the_run_and_leaves_the_rest_untouched(db):
    mailer = FakeMailer(fail={"a@a.pt": [SendError("throttled", "454 Throttling failure")]})
    sender, repo, _, _ = build(db, mailer)
    first, second = queue(repo, "a@a.pt"), queue(repo, "b@a.pt")
    summary = sender.run_once()
    assert summary.stopped.startswith("limitado_pelo_servidor") and summary.retried == 1
    assert repo.get(first).attempts == 1 and repo.get(second).attempts == 0
    assert mailer.sent == []


def test_consecutive_failures_stop_the_run(db):
    error = SendError("temporary", "451 later")
    mailer = FakeMailer(fail={a: [error] for a in ("a@a.pt", "b@a.pt", "c@a.pt")})
    sender, repo, _, _ = build(db, mailer, max_consecutive_failures=2)
    ids = [queue(repo, email) for email in ("a@a.pt", "b@a.pt", "c@a.pt")]
    summary = sender.run_once()
    assert summary.stopped == "falhas_consecutivas" and summary.retried == 2
    assert repo.get(ids[2]).attempts == 0


def test_the_daily_cap_stops_the_run_and_resets_the_next_day(db):
    mailer = FakeMailer()
    today, repo, _, _ = build(db, mailer, cap=1)
    queue(repo, "a@a.pt")
    queue(repo, "b@a.pt")
    first = today.run_once()
    assert first.sent == 1 and first.stopped == "limite_diario"
    again = today.run_once()
    assert again.sent == 0 and again.stopped == "limite_diario"
    tomorrow, _, _, _ = build(db, mailer, now=T0 + timedelta(days=1), cap=1)
    assert tomorrow.run_once().sent == 1
    assert sorted(m[0] for m in mailer.sent) == ["a@a.pt", "b@a.pt"]


def test_a_zero_cap_sends_nothing(db):
    mailer = FakeMailer()
    sender, repo, _, _ = build(db, mailer, cap=0)
    queue(repo)
    assert sender.run_once().stopped == "limite_diario" and mailer.sent == []


def test_the_worker_command_sends_the_pending_confirmations(db, tmp_path, monkeypatch):
    if db.kind != "sqlite":
        pytest.skip("O comando usa o ficheiro SQLite; o PostgreSQL usa o mesmo código.")
    from mini_marketo.database import connect, init_db
    from mini_marketo.repositories.subscription_repository import SubscriptionRepository

    path = tmp_path / "worker.db"
    conn = connect(path)
    init_db(conn)
    now = utc_now()
    request_id = SubscriptionRepository(conn).add_limited(
        "a@a.pt", "Ana", "Silva", "pt", ("newsletter",), now, now - timedelta(hours=24), 3
    )
    conn.close()
    sent = []

    class FakeSmtp:
        def __init__(self, config):
            pass

        def send(self, to, subject, text, html=None, headers=None):
            sent.append((to, subject, text))

        def close(self):
            pass

    clear_environment(monkeypatch)
    for name in (
        "MINI_MARKETO_DATABASE_URL", "MINI_MARKETO_PRIVACY_URL",
        "MINI_MARKETO_CONFIRMATION_DAILY_CAP", "MINI_MARKETO_SMTP_DAILY_CAP",
        "MINI_MARKETO_SMTP_MAX_ATTEMPTS", "MINI_MARKETO_SMTP_BATCH_SIZE",
    ):
        monkeypatch.delenv(name, raising=False)
    for name, value in {
        "MINI_MARKETO_SMTP_HOST": "smtp.exemplo.pt",
        "MINI_MARKETO_SMTP_SENDER": "no-reply@exemplo.pt",
        "MINI_MARKETO_SMTP_RATE": "1000",
        "MINI_MARKETO_PUBLIC_URL": "https://marketing.exemplo.pt",
        "MINI_MARKETO_SECRET_KEY": "k" * 40,
    }.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(send_worker, "SmtpMailer", FakeSmtp)
    monkeypatch.setattr(send_worker, "DEFAULT_DB", path)
    assert send_worker.main([]) == 0
    assert [m[0] for m in sent] == ["a@a.pt"]
    assert LINKS_OK.url_for(request_id) in sent[0][2]
    conn = connect(path)
    try:
        assert SubscriptionRepository(conn).get(request_id).delivery_status == "sent"
    finally:
        conn.close()


def test_the_worker_command_refuses_an_invalid_confirmation_configuration(monkeypatch, capsys):
    clear_environment(monkeypatch)
    for name, value in {
        "MINI_MARKETO_SMTP_HOST": "smtp.exemplo.pt",
        "MINI_MARKETO_SMTP_SENDER": "no-reply@exemplo.pt",
        "MINI_MARKETO_PUBLIC_URL": "https://marketing.exemplo.pt",
        "MINI_MARKETO_SECRET_KEY": "k" * 40,
        "MINI_MARKETO_CONFIRMATION_DAILY_CAP": "muitos",
    }.items():
        monkeypatch.setenv(name, value)
    assert send_worker.main([]) == 2
    assert "MINI_MARKETO_CONFIRMATION_DAILY_CAP" in capsys.readouterr().err
    monkeypatch.setenv("MINI_MARKETO_CONFIRMATION_DAILY_CAP", "10")
    monkeypatch.setenv("MINI_MARKETO_PRIVACY_URL", "nao-e-um-url")
    assert send_worker.main([]) == 2
    assert "MINI_MARKETO_PRIVACY_URL" in capsys.readouterr().err
