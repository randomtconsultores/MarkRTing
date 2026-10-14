import smtplib
import ssl

import pytest

from mini_marketo import mailer as mailer_module
from mini_marketo.mailer import ConfigError, SendError, SmtpConfig, SmtpMailer, classify


def config(**overrides):
    data = dict(
        host="smtp.exemplo.pt",
        port=587,
        sender="Mini <no-reply@exemplo.pt>",
        username="u",
        password="segredo",
    )
    data.update(overrides)
    return SmtpConfig(**data)


# --- configuração ---


def test_valid_config_hides_the_password_in_repr():
    assert "segredo" not in repr(config())


@pytest.mark.parametrize(
    "overrides",
    [
        {"host": ""},
        {"sender": "sem-arroba"},
        {"security": "plain"},
        {"security": "none", "username": "", "password": ""},
        {"port": 0},
        {"port": 70000},
        {"password": ""},
        {"username": ""},
        {"timeout": 0},
    ],
)
def test_invalid_config_is_rejected(overrides):
    with pytest.raises(ConfigError):
        config(**overrides)


def test_no_tls_is_accepted_only_for_a_local_server():
    local = config(host="localhost", security="none", port=25, username="", password="")
    assert local.security == "none"
    with pytest.raises(ConfigError, match="local"):
        config(host="smtp.exemplo.pt", security="none")


def test_from_env_uses_the_default_port_of_each_mode():
    base = {"MINI_MARKETO_SMTP_HOST": "smtp.exemplo.pt", "MINI_MARKETO_SMTP_SENDER": "a@b.pt"}
    assert SmtpConfig.from_env(base).port == 587
    assert SmtpConfig.from_env({**base, "MINI_MARKETO_SMTP_SECURITY": "ssl"}).port == 465
    local = {
        "MINI_MARKETO_SMTP_HOST": "localhost",
        "MINI_MARKETO_SMTP_SENDER": "a@b.pt",
        "MINI_MARKETO_SMTP_SECURITY": "none",
    }
    assert SmtpConfig.from_env(local).port == 25
    assert SmtpConfig.from_env({**base, "MINI_MARKETO_SMTP_PORT": "2525"}).port == 2525


def test_from_env_rejects_missing_or_malformed_values():
    with pytest.raises(ConfigError):
        SmtpConfig.from_env({})
    base = {"MINI_MARKETO_SMTP_HOST": "smtp.exemplo.pt", "MINI_MARKETO_SMTP_SENDER": "a@b.pt"}
    with pytest.raises(ConfigError):
        SmtpConfig.from_env({**base, "MINI_MARKETO_SMTP_PORT": "abc"})


# --- classificação de erros ---


@pytest.mark.parametrize(
    "exc, kind",
    [
        (smtplib.SMTPRecipientsRefused({"a@b.pt": (550, b"no such user")}), "permanent"),
        (smtplib.SMTPRecipientsRefused({"a@b.pt": (451, b"try later")}), "temporary"),
        (smtplib.SMTPDataError(554, b"blocked"), "permanent"),
        (smtplib.SMTPResponseException(451, b"try later"), "temporary"),
        (smtplib.SMTPResponseException(454, b"Throttling failure"), "throttled"),
        (smtplib.SMTPResponseException(421, b"too many connections"), "throttled"),
        (smtplib.SMTPAuthenticationError(535, b"auth failed"), "fatal"),
        (smtplib.SMTPSenderRefused(550, b"sender rejected", "a@b.pt"), "fatal"),
        (smtplib.SMTPNotSupportedError("STARTTLS não suportado"), "fatal"),
        (ssl.SSLCertVerificationError("certificado inválido"), "fatal"),
        (smtplib.SMTPConnectError(554, b"no service"), "temporary"),
        (smtplib.SMTPServerDisconnected("gone"), "temporary"),
        (TimeoutError("timeout"), "temporary"),
        (ConnectionRefusedError("refused"), "temporary"),
        (ValueError("Header values may not contain linefeed"), "permanent"),
        (RuntimeError("estranho"), "temporary"),
    ],
)
def test_classify_maps_errors_to_the_right_kind(exc, kind):
    error = classify(exc)
    assert isinstance(error, SendError) and error.kind == kind
    assert error.detail and len(error.detail) <= 300


def test_classify_keeps_an_existing_send_error_and_truncates_long_details():
    original = SendError("permanent", "550 x")
    assert classify(original) is original
    long = classify(smtplib.SMTPResponseException(451, b"x" * 1000))
    assert len(long.detail) == 300


# --- emissor com um servidor SMTP falso ---


class FakeSMTP:
    instances: list = []
    disconnects = 0
    refuse = None
    login_error = None

    def __init__(self, host, port, timeout=None, context=None):
        self.host, self.port, self.timeout, self.context = host, port, timeout, context
        self.calls, self.messages = [], []
        FakeSMTP.instances.append(self)

    def starttls(self, context=None):
        self.calls.append("starttls")

    def login(self, user, password):
        if FakeSMTP.login_error is not None:
            raise FakeSMTP.login_error
        self.calls.append(("login", user, password))

    def send_message(self, message):
        if FakeSMTP.disconnects > 0:
            FakeSMTP.disconnects -= 1
            raise smtplib.SMTPServerDisconnected("gone")
        if FakeSMTP.refuse is not None:
            raise FakeSMTP.refuse
        self.messages.append(message)

    def quit(self):
        self.calls.append("quit")


@pytest.fixture
def fake_smtp(monkeypatch):
    FakeSMTP.instances, FakeSMTP.disconnects = [], 0
    FakeSMTP.refuse, FakeSMTP.login_error = None, None
    monkeypatch.setattr(mailer_module.smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(mailer_module.smtplib, "SMTP_SSL", FakeSMTP)
    return FakeSMTP


def test_starttls_login_and_message_content(fake_smtp):
    mailer = SmtpMailer(config())
    mailer.send("ana@a.pt", "Olá", "texto", "<p>html</p>", {"X-Teste": "1"})
    smtp = fake_smtp.instances[0]
    assert (smtp.host, smtp.port) == ("smtp.exemplo.pt", 587)
    assert smtp.calls == ["starttls", ("login", "u", "segredo")]
    message = smtp.messages[0]
    assert message["To"] == "ana@a.pt" and message["From"] == "Mini <no-reply@exemplo.pt>"
    assert str(message["Subject"]) == "Olá" and message["X-Teste"] == "1"
    assert message["Message-ID"].endswith("@exemplo.pt>") and message["Date"]
    assert message.get_body(preferencelist=("plain",)).get_content().strip() == "texto"
    assert message.get_body(preferencelist=("html",)).get_content().strip() == "<p>html</p>"


def test_plain_text_message_has_no_html_part(fake_smtp):
    SmtpMailer(config()).send("ana@a.pt", "Assunto", "texto")
    message = fake_smtp.instances[0].messages[0]
    assert message.get_body(preferencelist=("html",)) is None


def test_ssl_mode_does_not_use_starttls(fake_smtp):
    SmtpMailer(config(security="ssl", port=465)).send("ana@a.pt", "A", "t")
    assert fake_smtp.instances[0].calls == [("login", "u", "segredo")]


def test_local_server_without_tls_or_credentials(fake_smtp):
    local = config(host="localhost", security="none", port=25, username="", password="")
    SmtpMailer(local).send("ana@a.pt", "A", "t")
    assert fake_smtp.instances[0].calls == []


def test_the_connection_is_reused_between_messages(fake_smtp):
    mailer = SmtpMailer(config())
    mailer.send("ana@a.pt", "A", "t")
    mailer.send("rui@a.pt", "B", "t")
    assert len(fake_smtp.instances) == 1 and len(fake_smtp.instances[0].messages) == 2


def test_it_reconnects_once_when_the_server_drops_the_connection(fake_smtp):
    fake_smtp.disconnects = 1
    SmtpMailer(config()).send("ana@a.pt", "A", "t")
    assert len(fake_smtp.instances) == 2
    assert len(fake_smtp.instances[1].messages) == 1


def test_two_consecutive_disconnects_become_a_temporary_error(fake_smtp):
    fake_smtp.disconnects = 2
    with pytest.raises(SendError) as info:
        SmtpMailer(config()).send("ana@a.pt", "A", "t")
    assert info.value.kind == "temporary"


def test_a_refused_recipient_is_permanent_and_keeps_the_connection(fake_smtp):
    mailer = SmtpMailer(config())
    fake_smtp.refuse = smtplib.SMTPRecipientsRefused({"a@b.pt": (550, b"no such user")})
    with pytest.raises(SendError) as info:
        mailer.send("a@b.pt", "A", "t")
    assert info.value.kind == "permanent"
    fake_smtp.refuse = None
    mailer.send("ana@a.pt", "A", "t")
    assert len(fake_smtp.instances) == 1


def test_authentication_failure_is_fatal_and_closes_the_connection(fake_smtp):
    fake_smtp.login_error = smtplib.SMTPAuthenticationError(535, b"auth failed")
    mailer = SmtpMailer(config())
    with pytest.raises(SendError) as info:
        mailer.send("ana@a.pt", "A", "t")
    assert info.value.kind == "fatal" and "535" in info.value.detail
    assert "segredo" not in info.value.detail
    assert "quit" in fake_smtp.instances[0].calls
    fake_smtp.login_error = None
    mailer.send("ana@a.pt", "A", "t")
    assert len(fake_smtp.instances) == 2


def test_a_header_injection_attempt_never_reaches_the_server(fake_smtp):
    with pytest.raises(SendError) as info:
        SmtpMailer(config()).send("a@b.pt\r\nBcc: x@y.z", "A", "t")
    assert info.value.kind == "permanent"
    assert fake_smtp.instances == []


def test_close_quits_and_is_idempotent(fake_smtp):
    mailer = SmtpMailer(config())
    mailer.send("ana@a.pt", "A", "t")
    mailer.close()
    mailer.close()
    assert fake_smtp.instances[0].calls.count("quit") == 1
