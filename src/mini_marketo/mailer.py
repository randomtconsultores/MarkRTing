import os
import re
import smtplib
import ssl
from collections.abc import Mapping
from dataclasses import dataclass, field
from email.message import EmailMessage
from email.utils import formatdate, make_msgid, parseaddr

LOOPBACK_HOSTS = ("localhost", "127.0.0.1", "::1")
SECURITY_MODES = ("starttls", "ssl", "none")
DEFAULT_PORTS = {"starttls": 587, "ssl": 465, "none": 25}
THROTTLE_CODES = (421, 454)
MAX_DETAIL = 300

# Códigos melhorados (RFC 3463) que dizem que o ENDEREÇO do destinatário não serve: 5.1.1 caixa
# inexistente, 5.1.2 domínio inexistente, 5.1.3 sintaxe inválida, 5.1.6 caixa mudada e 5.1.10
# domínio sem MX. Ficam de fora as recusas por política (5.7.x), caixa cheia (5.2.x) e os códigos
# do remetente (5.1.7 e 5.1.8): não dizem nada sobre o destinatário.
_ADDRESS_REJECTION = re.compile(r"\b5\.1\.(?:10|1|2|3|6)\b")


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class SmtpConfig:
    """Configuração do servidor SMTP (relay comercial ou Postfix próprio: é igual para a app).

    A palavra-passe não aparece no `repr`. Sem TLS só se aceita um servidor local.
    """

    host: str
    port: int
    sender: str
    security: str = "starttls"
    username: str = ""
    password: str = field(default="", repr=False)
    timeout: float = 30.0

    def __post_init__(self):
        if not self.host:
            raise ConfigError("MINI_MARKETO_SMTP_HOST é obrigatório.")
        if "@" not in parseaddr(self.sender)[1]:
            raise ConfigError("MINI_MARKETO_SMTP_SENDER tem de ser um endereço de email.")
        if self.security not in SECURITY_MODES:
            raise ConfigError("MINI_MARKETO_SMTP_SECURITY tem de ser starttls, ssl ou none.")
        if self.security == "none" and self.host not in LOOPBACK_HOSTS:
            raise ConfigError("Sem TLS (none) só é aceite para um servidor local.")
        if not 1 <= self.port <= 65535:
            raise ConfigError("A porta SMTP tem de estar entre 1 e 65535.")
        if bool(self.username) != bool(self.password):
            raise ConfigError("Utilizador e palavra-passe têm de ser definidos em conjunto.")
        if self.timeout <= 0:
            raise ConfigError("O tempo limite SMTP tem de ser positivo.")

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "SmtpConfig":
        env = os.environ if env is None else env
        security = env.get("MINI_MARKETO_SMTP_SECURITY", "starttls").strip().lower()
        raw_port = env.get("MINI_MARKETO_SMTP_PORT", "").strip()
        try:
            port = int(raw_port) if raw_port else DEFAULT_PORTS.get(security, 587)
            timeout = float(env.get("MINI_MARKETO_SMTP_TIMEOUT", "30"))
        except ValueError as exc:
            raise ConfigError(
                "MINI_MARKETO_SMTP_PORT e MINI_MARKETO_SMTP_TIMEOUT têm de ser números."
            ) from exc
        return cls(
            host=env.get("MINI_MARKETO_SMTP_HOST", "").strip(),
            port=port,
            sender=env.get("MINI_MARKETO_SMTP_SENDER", "").strip(),
            security=security,
            username=env.get("MINI_MARKETO_SMTP_USERNAME", ""),
            password=env.get("MINI_MARKETO_SMTP_PASSWORD", ""),
            timeout=timeout,
        )


class SendError(Exception):
    """Falha de envio já classificada.

    kind: "permanent" (não repetir), "temporary" (repetir mais tarde), "throttled" (o servidor
    pede abrandamento) ou "fatal" (configuração ou credenciais: parar tudo).

    `address_rejected`: o servidor recusou o destinatário com um código que diz que o endereço
    não serve (ver `is_address_rejection`). Só então o endereço pode ser suprimido.
    """

    def __init__(self, kind: str, detail: str, address_rejected: bool = False):
        super().__init__(detail)
        self.kind = kind
        self.detail = detail
        self.address_rejected = address_rejected


def is_address_rejection(text: str) -> bool:
    return bool(_ADDRESS_REJECTION.search(text))


def _text(value) -> str:
    return value.decode("utf-8", "replace") if isinstance(value, bytes) else str(value)


def _describe(exc: BaseException) -> str:
    if isinstance(exc, smtplib.SMTPResponseException):
        return f"{exc.smtp_code} {_text(exc.smtp_error)}"
    return str(exc) or type(exc).__name__


def _short(value: str) -> str:
    value = " ".join(value.split())
    return value[:MAX_DETAIL]


def classify(exc: BaseException) -> SendError:
    """Traduz uma exceção de envio numa SendError. 4xx é temporário e 5xx é permanente; erros
    de autenticação, remetente ou certificado são fatais (não são culpa do destinatário)."""
    if isinstance(exc, SendError):
        return exc
    if isinstance(
        exc,
        (
            smtplib.SMTPAuthenticationError,
            smtplib.SMTPSenderRefused,
            smtplib.SMTPNotSupportedError,
            smtplib.SMTPHeloError,
            ssl.SSLCertVerificationError,
        ),
    ):
        return SendError("fatal", _short(f"{type(exc).__name__}: {_describe(exc)}"))
    if isinstance(exc, smtplib.SMTPConnectError):
        return SendError("temporary", _short(f"Ligação recusada: {_describe(exc)}"))
    if isinstance(exc, smtplib.SMTPRecipientsRefused):
        refused = list(exc.recipients.values())
        code, message = refused[0] if refused else (550, b"")
        kind = "permanent" if 500 <= code < 600 else "temporary"
        text = _text(message)
        return SendError(
            kind,
            _short(f"{code} {text}"),
            address_rejected=kind == "permanent" and is_address_rejection(text),
        )
    if isinstance(exc, smtplib.SMTPResponseException):
        code = exc.smtp_code
        detail = _short(_describe(exc))
        if code in THROTTLE_CODES:
            return SendError("throttled", detail)
        if 500 <= code < 600:
            return SendError("permanent", detail)
        return SendError("temporary", detail)
    if isinstance(exc, ValueError):  # endereço ou cabeçalho inválido
        return SendError("permanent", _short(f"Mensagem inválida: {_describe(exc)}"))
    return SendError("temporary", _short(f"{type(exc).__name__}: {_describe(exc)}"))


class SmtpMailer:
    """Envia por SMTP, reutilizando a ligação entre mensagens e religando uma vez se cair."""

    def __init__(self, config: SmtpConfig):
        self.config = config
        self._conn = None

    def send(
        self,
        to: str,
        subject: str,
        text: str,
        html: str | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        try:
            message = self._build(to, subject, text, html, headers or {})
            self._deliver(message)
        except Exception as exc:
            raise classify(exc) from exc

    def close(self) -> None:
        self._drop()

    def _build(self, to, subject, text, html, headers) -> EmailMessage:
        sender = self.config.sender
        message = EmailMessage()
        message["From"] = sender
        message["To"] = to
        message["Subject"] = subject
        message["Date"] = formatdate(usegmt=True)
        message["Message-ID"] = make_msgid(domain=parseaddr(sender)[1].rpartition("@")[2])
        for name, value in headers.items():
            message[name] = value
        message.set_content(text)
        if html:
            message.add_alternative(html, subtype="html")
        return message

    def _deliver(self, message: EmailMessage) -> None:
        for attempt in (1, 2):
            try:
                if self._conn is None:
                    self._conn = self._open()
                self._conn.send_message(message)
                return
            except smtplib.SMTPServerDisconnected:
                self._drop()
                if attempt == 2:
                    raise
            except smtplib.SMTPRecipientsRefused:
                raise  # recusa de um destinatário: a ligação continua utilizável
            except Exception:
                self._drop()
                raise

    def _open(self):
        cfg = self.config
        if cfg.security == "ssl":
            conn = smtplib.SMTP_SSL(
                cfg.host, cfg.port, timeout=cfg.timeout, context=ssl.create_default_context()
            )
        else:
            conn = smtplib.SMTP(cfg.host, cfg.port, timeout=cfg.timeout)
        try:
            if cfg.security == "starttls":
                conn.starttls(context=ssl.create_default_context())
            if cfg.username:
                conn.login(cfg.username, cfg.password)
        except Exception:
            self._quiet_close(conn)
            raise
        return conn

    def _drop(self) -> None:
        conn, self._conn = self._conn, None
        if conn is not None:
            self._quiet_close(conn)

    @staticmethod
    def _quiet_close(conn) -> None:
        try:
            conn.quit()
        except Exception:
            pass
