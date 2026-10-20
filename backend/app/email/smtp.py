"""The service's own outbound e-mail over SMTP with TLS (services/system_mail.py).

* plain text, with the branded HTML as the alternative when there is one
* header injection defeated by stripping CR/LF from every header value
* addresses validated; credentials never logged
"""

from __future__ import annotations

import smtplib
import ssl
import uuid
from dataclasses import dataclass, field
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid, parseaddr

from email_validator import EmailNotValidError, validate_email

from ..core.errors import (
    IntegrationNotConfigured,
    PermanentExternalError,
    TransientExternalError,
    ValidationFailed,
)
from ..core.logging import get_logger, log_event
from ..core.sanitize import sanitize_header_field

log = get_logger(__name__)


@dataclass
class SmtpConfig:
    host: str
    port: int
    username: str
    password: str
    from_address: str
    use_tls: bool

    @property
    def configured(self) -> bool:
        return bool(self.host and self.from_address)


@dataclass
class OutboundMail:
    to: list[str]
    subject: str
    body_text: str
    body_html: str = ""
    message_id: str = ""  # set by the sender to match a returned e-mail to it later
    # Extra headers this mail needs (only In-Reply-To and References, for answers that stay in their thread).
    headers: dict[str, str] = field(default_factory=dict)
    # Files to attach: (file name, MIME type, content), e.g. a calendar invitation.
    attachments: list[tuple[str, str, bytes]] = field(default_factory=list)


def validate_address(address: str) -> str:
    name, addr = parseaddr(sanitize_header_field(address))
    try:
        valid = validate_email(addr, check_deliverability=False)
    except EmailNotValidError as exc:
        raise ValidationFailed(f"Invalid e-mail address: {addr!r}", code="INVALID_EMAIL") from exc
    return formataddr((sanitize_header_field(name), valid.normalized)) if name else valid.normalized


def build_message(cfg: SmtpConfig, mail: OutboundMail, *, sender_name: str = "") -> EmailMessage:
    message = EmailMessage()
    sender = validate_address(cfg.from_address)
    message["From"] = formataddr((sanitize_header_field(sender_name), parseaddr(sender)[1])) if sender_name else sender
    message["To"] = ", ".join(validate_address(a) for a in mail.to)
    message["Subject"] = sanitize_header_field(mail.subject)[:900]
    message["Date"] = formatdate(localtime=False)
    domain = parseaddr(sender)[1].split("@")[-1] or "localhost"
    message["Message-ID"] = sanitize_header_field(mail.message_id) if mail.message_id else make_msgid(idstring=uuid.uuid4().hex[:12], domain=domain)
    for name, value in mail.headers.items():
        if name in THREAD_HEADERS and value:
            message[name] = sanitize_header_field(value)[:2000]
    message.set_content(mail.body_text)
    if mail.body_html:
        # multipart/alternative: clients that show HTML show the design, the rest the text.
        message.add_alternative(mail.body_html, subtype="html")
    for filename, mime, data in mail.attachments:
        maintype, _, subtype = mime.partition("/")
        message.add_attachment(data, maintype=maintype, subtype=subtype or "octet-stream", filename=sanitize_header_field(filename)[:120])
    return message


THREAD_HEADERS = ("In-Reply-To", "References")


def send(cfg: SmtpConfig, mail: OutboundMail, *, sender_name: str = "", sent: list[bytes] | None = None) -> str:
    """Send; returns the Message-ID. `sent` receives the message as sent (to keep a copy in a Sent folder)."""
    if not cfg.host or not cfg.from_address:
        raise IntegrationNotConfigured("The service mailbox (SMTP_*) is not configured.", code="SYSTEM_MAIL_NOT_CONFIGURED")
    message = build_message(cfg, mail, sender_name=sender_name)
    recipients = [parseaddr(validate_address(a))[1] for a in mail.to]
    context = ssl.create_default_context()
    try:
        if cfg.port == 465:
            server: smtplib.SMTP = smtplib.SMTP_SSL(cfg.host, cfg.port, timeout=30, context=context)
        else:
            server = smtplib.SMTP(cfg.host, cfg.port, timeout=30)
        with server:
            server.ehlo()
            if cfg.use_tls and cfg.port != 465:
                server.starttls(context=context)
                server.ehlo()
            if cfg.username and cfg.password:
                server.login(cfg.username, cfg.password)
            refused = server.send_message(message, from_addr=parseaddr(cfg.from_address)[1], to_addrs=recipients)
    except smtplib.SMTPAuthenticationError as exc:
        raise PermanentExternalError("SMTP authentication failed — check the credentials.", code="SMTP_AUTH_FAILED") from exc
    except smtplib.SMTPRecipientsRefused as exc:
        raise PermanentExternalError(f"Recipient refused: {list(exc.recipients)[:3]}", code="SMTP_RECIPIENT_REFUSED") from exc
    except (smtplib.SMTPServerDisconnected, smtplib.SMTPConnectError, TimeoutError, OSError) as exc:
        raise TransientExternalError(f"SMTP connection problem: {exc.__class__.__name__}", code="SMTP_UNAVAILABLE") from exc
    except smtplib.SMTPException as exc:
        raise TransientExternalError(f"SMTP error: {exc.__class__.__name__}", code="SMTP_ERROR") from exc
    log_event(log, "email sent", to=len(recipients) - len(refused), refused=len(refused))
    if sent is not None:
        sent.append(message.as_bytes())
    return str(message["Message-ID"])
