"""E-mails the install itself sends: admin invitations, alerts, the weekly report.
What they say is in email/messages.py, how they look in email/layout.py: every one
is branded HTML with a plain-text alternative made from the same words.

They go out through the install's own mailbox (`SMTP_*` in the environment).

In test mode nothing is sent: messages land in `OUTBOX` for the tests to read.
Without a configured server outside production the message is kept in `OUTBOX`
and, in development only, its link is written to the log, so signing up works on
a laptop with no mail server. Production never logs a link.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import make_msgid

from sqlalchemy.orm import Session

from ..config import settings
from ..core.errors import AppError, IntegrationNotConfigured, TransientExternalError
from ..core.logging import get_logger, log_event
from ..email import smtp as smtp_client
from ..email.layout import Brand, render
from ..email.messages import MESSAGES, audience
from ..email.smtp import SmtpConfig
from . import mail_delivery

log = get_logger(__name__)


@dataclass
class SentSystemMail:
    to: str
    subject: str
    body: str
    kind: str
    at: datetime
    html: str = ""


OUTBOX: list[SentSystemMail] = []

def knows(kind: str) -> bool:
    """There is an e-mail for this kind."""
    return kind in MESSAGES


def system_smtp() -> SmtpConfig:
    cfg = settings()
    return SmtpConfig(
        host=cfg.smtp_host,
        port=cfg.smtp_port,
        username=cfg.smtp_login,
        password=cfg.smtp_password,
        from_address=cfg.smtp_from or cfg.smtp_login,
        use_tls=cfg.smtp_use_tls,
    )


def is_configured() -> bool:
    return system_smtp().configured


def base_url() -> str:
    return (settings().public_base_url or "http://localhost:5173").rstrip("/")


def brand(kind: str, language: str) -> Brand:
    cfg = settings()
    return Brand(
        app=cfg.app_name,
        # No logo until the install has its own (the header shows the name).
        logo_url="",
        site_url=cfg.public_site_url or cfg.public_base_url,
        imprint_url=cfg.imprint_url,
        privacy_url=cfg.privacy_url,
        operator=cfg.operator_name,
        owner=cfg.operator_owner,
        address=cfg.operator_address,
        support_email=cfg.support_email,
        audience=audience(kind),
        language=language,
    )


def compose(kind: str, *, to: str, name: str, locale: str, link: str = "", base: str = "", extra: dict[str, str] | None = None) -> SentSystemMail:
    """The e-mail, subject, plain text and HTML, without sending it."""
    # English unless the message exists in the reader's language.
    language = locale if locale in MESSAGES[kind] else "en"
    # A name is typed by whoever signs up; it must not carry a link into our mail.
    looks_like_link = re.search(r"://|www\.|\b[\w-]+\.[a-z]{2,}\b", name or "", re.IGNORECASE)
    safe_name = (name or "").strip() if name and not looks_like_link else ""
    values = {**(extra or {}), "name": safe_name, "link": link, "base": base or base_url()}
    subject, text, html = render(MESSAGES[kind][language], values, brand(kind, language))
    return SentSystemMail(to=to, subject=subject, body=text, kind=kind, at=datetime.now(UTC), html=html)


@dataclass(frozen=True)
class Sent:
    """The recorded e-mail: its delivery row and, when asked for, the private
    reference a page without sign-in uses to follow it (employer registration)."""

    delivery_id: int
    status: str
    handle: str = ""


def _message_id() -> str:
    sender = (system_smtp().from_address or "mail@localhost").split("@")[-1].strip("> ") or "localhost"
    return make_msgid(idstring=uuid.uuid4().hex[:12], domain=sender)


def deliver(to: str, subject: str, text: str, html: str, *, message_id: str) -> None:
    """Hand one e-mail to the mail server (or, in tests and on a laptop, keep it).
    Raises TransientExternalError when trying again later may help."""
    cfg = settings()
    server = system_smtp()
    if cfg.execution_mode == "test" or (not server.configured and not cfg.is_production):
        return
    if not server.configured:
        raise IntegrationNotConfigured("The service mailbox (SMTP_*) is not configured.", code="SYSTEM_MAIL_NOT_CONFIGURED")
    smtp_client.send(server, smtp_client.OutboundMail(to=[to], subject=subject, body_text=text, body_html=html, message_id=message_id), sender_name=cfg.app_name)


def send(kind: str, *, to: str, name: str, locale: str, link: str = "", base: str = "", extra: dict[str, str] | None = None,
         session: Session | None = None, owner: str = "", handle: bool = False) -> Sent:
    """Compose, record and send. Recorded in `session`, the transaction of the link
    it carries; `owner` says whose e-mail it is ("user:12", "employer_user:3").

    A temporary mail-server problem does not fail the caller: the e-mail is retried
    by the worker. A permanent one is recorded and raised, as before. Without a
    session (internal alerts to the team, the admin command line) it is sent
    unrecorded, as before: no second transaction is opened beside the caller's."""
    cfg = settings()
    if to.lower().endswith(".invalid"):
        # A person a partner added without an e-mail address (services/partners/people.py): there is nobody to mail.
        return Sent(0, "skipped")
    mail = compose(kind, to=to, name=name, locale=locale, link=link, base=base, extra=extra)
    message_id = _message_id()
    raw_handle = mail_delivery.new_handle() if handle else ""

    def run(db: Session) -> Sent:
        row = mail_delivery.record(db, kind=kind, owner=owner, recipient=to, language=locale if locale in MESSAGES[kind] else "en", message_id=message_id,
                                   payload=mail_delivery.seal(mail.subject, mail.body, mail.html), handle=raw_handle)
        kept = cfg.execution_mode == "test" or (not system_smtp().configured and not cfg.is_production)
        try:
            deliver(to, mail.subject, mail.body, mail.html, message_id=message_id)
        except TransientExternalError as exc:
            mail_delivery.mark_failed(row, exc)
            return Sent(row.id, row.status, raw_handle)
        except AppError as exc:
            mail_delivery.mark_failed(row, exc)
            raise
        mail_delivery.mark_sent(row)
        if kept:
            OUTBOX.append(mail)
            del OUTBOX[:-100]
            if cfg.execution_mode == "development":
                log_event(log, "system mail kept (not sent)", kind=kind, mode=cfg.execution_mode, to=to, link=link)
            else:
                log_event(log, "system mail kept (not sent)", kind=kind, mode=cfg.execution_mode)
        else:
            log_event(log, "system mail sent", kind=kind)
        return Sent(row.id, row.status, raw_handle)

    if session is not None:
        return run(session)
    deliver(to, mail.subject, mail.body, mail.html, message_id=message_id)
    if cfg.execution_mode == "test" or (not system_smtp().configured and not cfg.is_production):
        OUTBOX.append(mail)
        del OUTBOX[:-100]
    log_event(log, "system mail sent (unrecorded)", kind=kind)
    return Sent(0, "sent")
