from unittest.mock import Mock

import pytest

from mini_marketo import send_worker
from mini_marketo.delivery import RunSummary


@pytest.mark.parametrize(
    "stop,expected_calls,expected_code",
    [
        ("", 1, 0),
        ("limite_diario", 1, 0),
        ("limitado_pelo_servidor: 454 later", 0, 0),
        ("falhas_consecutivas", 0, 0),
        ("paragem_desconhecida", 0, 0),
        ("erro_fatal: auth", 0, 2),
    ],
)
def test_confirmation_stop_controls_the_campaign_phase(monkeypatch, stop, expected_calls, expected_code):
    conn, mailer, sender, worker = (Mock() for _ in range(4))
    sender.run_once.return_value = RunSummary(stopped=stop)
    worker.run_once.return_value = RunSummary()
    monkeypatch.setattr(send_worker.SmtpConfig, "from_env", lambda: object())
    monkeypatch.setattr(send_worker.UnsubscribeLinks, "from_env", lambda: object())
    monkeypatch.setattr(send_worker, "confirmation_cap_from_env", lambda: 200)
    monkeypatch.setattr(send_worker, "load_privacy_url", lambda: "")
    monkeypatch.setattr(send_worker, "open_repositories", lambda *args: (conn, None, None, None, None))
    monkeypatch.setattr(send_worker, "open_subscription_repository", lambda *args: None)
    monkeypatch.setattr(send_worker, "SmtpMailer", lambda *args: mailer)
    monkeypatch.setattr(send_worker, "ConfirmationSender", lambda *args: sender)
    monkeypatch.setattr(send_worker, "DeliveryWorker", lambda *args: worker)
    assert send_worker.main([]) == expected_code
    assert worker.run_once.call_count == expected_calls
    mailer.close.assert_called_once()
    conn.close.assert_called_once()


def test_loop_waits_before_retrying_a_throttled_round(monkeypatch):
    conn, mailer, sender, worker = (Mock() for _ in range(4))
    sender.run_once.return_value = RunSummary(stopped="limitado_pelo_servidor: 454 later")
    sleeps = []
    def pause(seconds):
        sleeps.append(seconds)
        raise KeyboardInterrupt
    monkeypatch.setattr(send_worker.SmtpConfig, "from_env", lambda: object())
    monkeypatch.setattr(send_worker.UnsubscribeLinks, "from_env", lambda: object())
    monkeypatch.setattr(send_worker, "confirmation_cap_from_env", lambda: 200)
    monkeypatch.setattr(send_worker, "load_privacy_url", lambda: "")
    monkeypatch.setattr(send_worker, "open_repositories", lambda *args: (conn, None, None, None, None))
    monkeypatch.setattr(send_worker, "open_subscription_repository", lambda *args: None)
    monkeypatch.setattr(send_worker, "SmtpMailer", lambda *args: mailer)
    monkeypatch.setattr(send_worker, "ConfirmationSender", lambda *args: sender)
    monkeypatch.setattr(send_worker, "DeliveryWorker", lambda *args: worker)
    monkeypatch.setattr(send_worker.time, "sleep", pause)
    assert send_worker.main(["--loop", "--poll-seconds", "1"]) == 0
    assert sleeps and sleeps[0] >= 60
    worker.run_once.assert_not_called()
    mailer.close.assert_called_once()
    conn.close.assert_called_once()


def test_poll_seconds_must_be_positive():
    with pytest.raises(SystemExit) as exc:
        send_worker.main(["--poll-seconds", "0"])
    assert exc.value.code == 2
