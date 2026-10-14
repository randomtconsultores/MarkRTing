from datetime import timedelta

import pytest
from campaign_support import T0, Setup

from mini_marketo import send_worker
from mini_marketo.delivery import DeliveryLimits, DeliveryWorker, backoff_seconds
from mini_marketo.mailer import SendError
from mini_marketo.services.unsubscribe_service import UnsubscribeService
from mini_marketo.unsubscribe import UnsubscribeLinks

LINKS = UnsubscribeLinks("https://marketing.exemplo.pt", "k" * 40)


class FakeMailer:
    """`fail`: endereço -> lista de erros (ou None) consumidos, um por chamada."""

    def __init__(self, fail=None):
        self.sent = []
        self.headers = {}
        self.fail = {to: list(errors) for to, errors in (fail or {}).items()}

    def send(self, to, subject, text, html=None, headers=None):
        queue = self.fail.get(to)
        if queue:
            error = queue.pop(0)
            if error is not None:
                raise error
        self.sent.append((to, subject, text, html))
        self.headers[to] = headers

    def close(self):
        pass


def make_worker(s, mailer, now=T0, links=LINKS, **limits):
    sleeps = []
    worker = DeliveryWorker(
        s.repo,
        s.segments.contacts,
        s.segments.consents,
        mailer,
        DeliveryLimits(**limits),
        links,
        now=lambda: now,
        sleep=sleeps.append,
    )
    return worker, sleeps


def queued(s, **campaign):
    c = s.campaign(**campaign)
    s.service.enqueue(c.id)
    return c


# --- sem base de dados ---


def test_backoff_doubles_and_is_capped():
    limits = DeliveryLimits()
    assert [backoff_seconds(n, limits) for n in (1, 2, 3, 4)] == [60, 120, 240, 480]
    assert backoff_seconds(20, limits) == 3600


@pytest.mark.parametrize(
    "overrides",
    [
        {"rate_per_second": 0},
        {"daily_cap": -1},
        {"max_attempts": 0},
        {"batch_size": 0},
        {"max_consecutive_failures": 0},
    ],
)
def test_invalid_limits_are_rejected(overrides):
    with pytest.raises(ValueError):
        DeliveryLimits(**overrides)


def test_limits_from_env():
    assert DeliveryLimits.from_env({}) == DeliveryLimits()
    env = {
        "MINI_MARKETO_SMTP_RATE": "5",
        "MINI_MARKETO_SMTP_DAILY_CAP": "1000",
        "MINI_MARKETO_SMTP_MAX_ATTEMPTS": "3",
        "MINI_MARKETO_SMTP_BATCH_SIZE": "10",
    }
    limits = DeliveryLimits.from_env(env)
    assert (limits.rate_per_second, limits.daily_cap) == (5.0, 1000)
    assert (limits.max_attempts, limits.batch_size) == (3, 10)
    with pytest.raises(ValueError, match="MINI_MARKETO_SMTP_RATE"):
        DeliveryLimits.from_env({"MINI_MARKETO_SMTP_RATE": "depressa"})


def clear_environment(monkeypatch):
    for name in ("HOST", "SENDER", "PORT", "SECURITY", "USERNAME", "PASSWORD"):
        monkeypatch.delenv(f"MINI_MARKETO_SMTP_{name}", raising=False)
    for name in ("PUBLIC_URL", "SECRET_KEY", "SECRET_KEY_PREVIOUS"):
        monkeypatch.delenv(f"MINI_MARKETO_{name}", raising=False)


def test_the_command_refuses_to_start_without_smtp_configuration(monkeypatch, capsys):
    clear_environment(monkeypatch)
    assert send_worker.main([]) == 2
    assert "Configuração inválida" in capsys.readouterr().err


def test_the_command_refuses_to_start_without_the_unsubscribe_configuration(monkeypatch, capsys):
    clear_environment(monkeypatch)
    monkeypatch.setenv("MINI_MARKETO_SMTP_HOST", "smtp.exemplo.pt")
    monkeypatch.setenv("MINI_MARKETO_SMTP_SENDER", "no-reply@exemplo.pt")
    assert send_worker.main([]) == 2
    assert "MINI_MARKETO_PUBLIC_URL" in capsys.readouterr().err
    monkeypatch.setenv("MINI_MARKETO_PUBLIC_URL", "https://marketing.exemplo.pt")
    assert send_worker.main([]) == 2
    assert "MINI_MARKETO_SECRET_KEY" in capsys.readouterr().err


def test_a_worker_cannot_be_built_without_unsubscribe_links():
    with pytest.raises(ValueError, match="cancelamento"):
        DeliveryWorker(None, None, None, None, DeliveryLimits(), None)


# --- com base de dados, nos dois backends ---


def test_sends_only_to_eligible_contacts_with_the_rendered_content(db):
    s = Setup(db)
    c = queued(s, body_html="<p>{first_name}</p>")
    mailer = FakeMailer()
    worker, sleeps = make_worker(s, mailer, rate_per_second=2)
    summary = worker.run_once()
    assert (summary.sent, summary.skipped, summary.failed, summary.retried) == (2, 0, 0, 0)
    assert summary.stopped == ""
    by_address = {m[0]: m for m in mailer.sent}
    assert set(by_address) == {"ana@a.pt", "rui@a.pt"}
    _, subject, text, html = by_address["ana@a.pt"]
    assert subject == "Olá Ana"
    assert text.startswith("Olá Ana Silva")
    assert html.startswith("<p>Ana</p>")
    assert s.repo.counts(c.id) == {"sent": 2, "skipped": 1}
    assert sleeps == [0.5, 0.5]


def test_every_email_carries_the_unsubscribe_link_and_the_one_click_headers(db):
    s = Setup(db)
    c = queued(s, body_html="<p>{first_name}</p>")
    mailer = FakeMailer()
    make_worker(s, mailer)[0].run_once()
    for to, _, text, html in mailer.sent:
        message = next(m for m in s.repo.list_messages(c.id) if m.email == to)
        url = LINKS.url_for(message.id)
        headers = mailer.headers[to]
        assert headers["List-Unsubscribe"] == f"<{url}>"
        assert headers["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"
        assert url in text and f'href="{url}"' in html


def test_the_link_in_a_sent_email_really_unsubscribes_that_contact(db):
    s = Setup(db)
    c = queued(s)
    mailer = FakeMailer()
    make_worker(s, mailer)[0].run_once()
    header = mailer.headers["ana@a.pt"]["List-Unsubscribe"]
    token = header.strip("<>").rsplit("/", 1)[1]
    service = UnsubscribeService(s.repo, s.segments.consents, LINKS)
    assert service.unsubscribe(token) is not None
    assert s.consents.status(s.ana.id, "newsletter") == "withdrawn"
    assert s.consents.status(s.rui.id, "newsletter") == "granted"
    assert s.message_for(c.id, s.ana).status == "sent"


def test_a_text_only_campaign_is_sent_without_an_html_part(db):
    s = Setup(db)
    queued(s)
    mailer = FakeMailer()
    make_worker(s, mailer)[0].run_once()
    assert all(m[3] is None for m in mailer.sent)


def test_a_second_run_sends_nothing_more(db):
    s = Setup(db)
    queued(s)
    mailer = FakeMailer()
    worker, _ = make_worker(s, mailer)
    worker.run_once()
    again = worker.run_once()
    assert again.sent == 0 and len(mailer.sent) == 2


def test_a_batch_smaller_than_the_queue_still_sends_everything(db):
    s = Setup(db)
    queued(s)
    mailer = FakeMailer()
    worker, _ = make_worker(s, mailer, batch_size=1)
    assert worker.run_once().sent == 2


def test_withdrawal_after_enqueue_is_respected_at_send_time(db):
    s = Setup(db)
    c = queued(s)
    s.consents.record(s.rui.id, "newsletter", "withdrawn", "admin")
    mailer = FakeMailer()
    worker, _ = make_worker(s, mailer)
    summary = worker.run_once()
    assert (summary.sent, summary.skipped) == (1, 1)
    rui = s.message_for(c.id, s.rui)
    assert rui.status == "skipped" and rui.skip_reason == "sem_consentimento"
    assert [m[0] for m in mailer.sent] == ["ana@a.pt"]


def test_an_unsubscribe_after_enqueue_is_respected_at_send_time(db):
    s = Setup(db)
    c = queued(s)
    token = LINKS.token_for(s.message_for(c.id, s.rui).id)
    UnsubscribeService(s.repo, s.segments.consents, LINKS).unsubscribe(token)
    mailer = FakeMailer()
    summary = make_worker(s, mailer)[0].run_once()
    assert (summary.sent, summary.skipped) == (1, 1)
    assert [m[0] for m in mailer.sent] == ["ana@a.pt"]


def test_an_email_removed_after_enqueue_is_not_used(db):
    s = Setup(db)
    c = queued(s)
    s.contacts.add_email(s.ana.id, "ana2@a.pt")
    s.contacts.set_primary_email(s.ana.id, "ana2@a.pt")
    s.contacts.remove_email(s.ana.id, "ana@a.pt")
    mailer = FakeMailer()
    worker, _ = make_worker(s, mailer)
    worker.run_once()
    ana = s.message_for(c.id, s.ana)
    assert ana.status == "skipped" and ana.skip_reason == "email_removido"
    assert [m[0] for m in mailer.sent] == ["rui@a.pt"]


def test_a_permanent_failure_is_final_and_does_not_block_the_others(db):
    s = Setup(db)
    c = queued(s)
    mailer = FakeMailer(fail={"ana@a.pt": [SendError("permanent", "550 no such user")]})
    worker, _ = make_worker(s, mailer)
    summary = worker.run_once()
    assert (summary.sent, summary.failed) == (1, 1)
    ana = s.message_for(c.id, s.ana)
    assert ana.status == "failed" and ana.last_error == "550 no such user" and ana.attempts == 1
    assert worker.run_once().sent == 0
    assert s.message_for(c.id, s.ana).attempts == 1


def test_a_temporary_failure_is_retried_after_the_backoff(db):
    s = Setup(db)
    c = queued(s)
    mailer = FakeMailer(fail={"ana@a.pt": [SendError("temporary", "451 later")]})
    first, _ = make_worker(s, mailer, now=T0)
    summary = first.run_once()
    assert (summary.retried, summary.sent) == (1, 1)
    ana = s.message_for(c.id, s.ana)
    assert ana.status == "pending" and ana.attempts == 1 and ana.last_error == "451 later"
    assert ana.next_attempt_at == T0 + timedelta(seconds=60)
    too_early, _ = make_worker(s, mailer, now=T0 + timedelta(seconds=30))
    assert too_early.run_once().sent == 0
    on_time, _ = make_worker(s, mailer, now=T0 + timedelta(seconds=61))
    assert on_time.run_once().sent == 1
    assert s.message_for(c.id, s.ana).status == "sent"


def test_attempts_run_out(db):
    s = Setup(db)
    c = queued(s)
    error = SendError("temporary", "451 later")
    mailer = FakeMailer(fail={"ana@a.pt": [error, error]})
    first, _ = make_worker(s, mailer, now=T0, max_attempts=2)
    first.run_once()
    second, _ = make_worker(s, mailer, now=T0 + timedelta(hours=1), max_attempts=2)
    summary = second.run_once()
    assert summary.failed == 1
    ana = s.message_for(c.id, s.ana)
    assert ana.status == "failed" and ana.attempts == 2
    assert "tentativas esgotadas" in ana.last_error


def test_a_fatal_error_stops_the_run_and_keeps_everything_pending(db):
    s = Setup(db)
    c = queued(s)
    fatal = SendError("fatal", "535 auth failed")
    mailer = FakeMailer(fail={"ana@a.pt": [fatal], "rui@a.pt": [fatal]})
    worker, _ = make_worker(s, mailer)
    summary = worker.run_once()
    assert summary.stopped.startswith("erro_fatal")
    assert mailer.sent == []
    assert s.repo.counts(c.id) == {"pending": 2, "skipped": 1}
    pending = [m for m in s.repo.list_messages(c.id) if m.status == "pending"]
    assert [m.attempts for m in pending] == [0, 0]


def test_throttling_stops_the_run_and_reschedules_only_the_current_message(db):
    s = Setup(db)
    c = queued(s)
    throttled = SendError("throttled", "454 Throttling failure")
    mailer = FakeMailer(fail={"ana@a.pt": [throttled], "rui@a.pt": [throttled]})
    worker, _ = make_worker(s, mailer, now=T0)
    summary = worker.run_once()
    assert summary.stopped.startswith("limitado_pelo_servidor") and summary.retried == 1
    pending = [m for m in s.repo.list_messages(c.id) if m.status == "pending"]
    assert sorted(m.attempts for m in pending) == [0, 1]
    assert sum(1 for m in pending if m.next_attempt_at > T0) == 1


def test_the_daily_cap_stops_the_run_and_resets_the_next_day(db):
    s = Setup(db)
    c = queued(s)
    mailer = FakeMailer()
    today, _ = make_worker(s, mailer, now=T0, daily_cap=1)
    first = today.run_once()
    assert first.sent == 1 and first.stopped == "limite_diario"
    assert s.repo.counts(c.id) == {"sent": 1, "pending": 1, "skipped": 1}
    again = today.run_once()
    assert again.sent == 0 and again.stopped == "limite_diario"
    tomorrow, _ = make_worker(s, mailer, now=T0 + timedelta(days=1), daily_cap=1)
    assert tomorrow.run_once().sent == 1
    assert s.repo.counts(c.id) == {"sent": 2, "skipped": 1}


def test_consecutive_failures_stop_the_run_and_release_the_rest(db):
    s = Setup(db)
    zed = s.contacts.create_contact("Zed", "Z", "zed@a.pt")
    s.consents.record(zed.id, "newsletter", "granted", "admin")
    c = queued(s)
    error = SendError("temporary", "451 later")
    mailer = FakeMailer(fail={a: [error] for a in ("ana@a.pt", "rui@a.pt", "zed@a.pt")})
    worker, _ = make_worker(s, mailer, now=T0, max_consecutive_failures=2)
    summary = worker.run_once()
    assert summary.stopped == "falhas_consecutivas" and summary.retried == 2
    assert s.repo.counts(c.id) == {"pending": 3, "skipped": 1}
    pending = [m for m in s.repo.list_messages(c.id) if m.status == "pending"]
    assert sorted(m.attempts for m in pending) == [0, 1, 1]


def test_a_success_resets_the_failure_streak(db):
    s = Setup(db)
    zed = s.contacts.create_contact("Zed", "Z", "zed@a.pt")
    s.consents.record(zed.id, "newsletter", "granted", "admin")
    queued(s)
    error = SendError("temporary", "451 later")
    mailer = FakeMailer(fail={"rui@a.pt": [error]})
    worker, _ = make_worker(s, mailer, now=T0, max_consecutive_failures=2)
    summary = worker.run_once()
    assert summary.stopped == "" and summary.sent == 2 and summary.retried == 1


def test_messages_left_claimed_by_a_dead_worker_are_recovered(db):
    s = Setup(db)
    c = queued(s)
    s.repo.claim_due(T0, 10)  # um processo que reclamou e morreu
    mailer = FakeMailer()
    worker, _ = make_worker(s, mailer, now=T0 + timedelta(minutes=16))
    assert worker.run_once().sent == 2
    assert all(m.attempts == 2 for m in s.repo.list_messages(c.id) if m.status == "sent")


def test_an_unexpected_exception_from_the_mailer_is_treated_as_temporary(db):
    s = Setup(db)
    c = queued(s)
    mailer = FakeMailer(fail={"ana@a.pt": [RuntimeError("estranho")]})
    worker, _ = make_worker(s, mailer, now=T0)
    summary = worker.run_once()
    assert summary.retried == 1 and summary.sent == 1
    ana = s.message_for(c.id, s.ana)
    assert ana.status == "pending" and "RuntimeError" in ana.last_error
