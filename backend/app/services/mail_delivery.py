"""What became of every e-mail the service sends (models/mail.py).

- Each e-mail is recorded in the same transaction as the link it carries, then
  sent at once. A temporary failure (the mail server busy or unreachable) is
  retried by the worker after 1, 5 and 30 minutes, 2 and 6 hours, then given up.
- A newer e-mail of the same kind to the same person supersedes older ones still
  waiting, so an old link never arrives after a new one.
- The returned-mail check (services/mail_bounces.py) marks an e-mail `bounced`
  with the reason (no such address, mailbox full, blocked), which the pages show
  so the person can correct the address.

The content is stored encrypted, and only until the e-mail is settled.
"""

from __future__ import annotations

import hashlib
import json
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from ..core import crypto
from ..core.errors import AppError, PermanentExternalError, TransientExternalError
from ..core.logging import get_logger, log_event
from ..models import EmailDelivery

log = get_logger(__name__)

# Waits before each retry; after the last one the e-mail is given up.
RETRY_AFTER = (timedelta(minutes=1), timedelta(minutes=5), timedelta(minutes=30), timedelta(hours=2), timedelta(hours=6))
WAITING = ("sending", "retrying")
_AAD = "email-delivery"


@dataclass(frozen=True)
class Status:
    """What a page may show about the latest e-mail of a kind."""

    status: str  # sent | retrying | failed | bounced
    reason: str
    recipient: str
    sent_at: datetime | None
    created_at: datetime


def handle_digest(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def new_handle() -> str:
    return secrets.token_urlsafe(24)


def seal(subject: str, text: str, html: str) -> str:
    if not crypto.is_configured():
        return ""
    return crypto.encrypt(json.dumps({"subject": subject, "text": text, "html": html}), associated_data=_AAD)


def unseal(token: str) -> dict[str, str] | None:
    if not token:
        return None
    try:
        return dict(json.loads(crypto.decrypt(token, associated_data=_AAD)))
    except (AppError, ValueError):
        return None


def record(session: Session, *, kind: str, owner: str, recipient: str, language: str, message_id: str, payload: str, handle: str = "") -> EmailDelivery:
    """Server work: the e-mail about to be sent. Older ones of this kind for this owner, still waiting, step aside."""
    now = datetime.now(UTC)
    if owner:
        # A statement, not a read: the admin connection may not read the content column.
        session.execute(update(EmailDelivery).where(EmailDelivery.owner == owner, EmailDelivery.kind == kind, EmailDelivery.status.in_(WAITING))
                        .values(status="superseded", payload_encrypted="", settled_at=now, updated_at=now))
    row = EmailDelivery(kind=kind, owner=owner, recipient=recipient, language=language, message_id=message_id, status="sending", attempts=0,
                        payload_encrypted=payload, handle_hash=handle_digest(handle) if handle else "", created_at=now, updated_at=now)
    session.add(row)
    session.flush()
    return row


def cancel(session: Session, owner: str) -> None:
    """The account an e-mail belongs to is gone: nothing still waiting for it is sent,
    and the pages that followed its e-mails no longer open (a new account may later
    get the same number)."""
    now = datetime.now(UTC)
    session.execute(update(EmailDelivery).where(EmailDelivery.owner == owner, EmailDelivery.status.in_(WAITING))
                    .values(status="superseded", payload_encrypted="", settled_at=now, updated_at=now))
    session.execute(update(EmailDelivery).where(EmailDelivery.owner == owner, EmailDelivery.handle_hash != "").values(handle_hash="", updated_at=now))


def mark_sent(row: EmailDelivery) -> None:
    now = datetime.now(UTC)
    row.attempts += 1
    row.status, row.sent_at, row.next_attempt_at, row.payload_encrypted, row.last_error, row.updated_at = "sent", now, None, "", "", now


def mark_failed(row: EmailDelivery, exc: Exception) -> None:
    """A temporary problem waits for the next retry; anything else, or the last retry, gives up."""
    now = datetime.now(UTC)
    row.attempts += 1
    row.last_error = (exc.code if isinstance(exc, AppError) else type(exc).__name__)[:80]
    if isinstance(exc, TransientExternalError) and row.attempts <= len(RETRY_AFTER) and row.payload_encrypted:
        row.status, row.next_attempt_at = "retrying", now + RETRY_AFTER[row.attempts - 1]
    else:
        row.status, row.next_attempt_at, row.settled_at, row.payload_encrypted = "failed", None, now, ""
    row.updated_at = now
    log_event(log, "e-mail not delivered yet", kind=row.kind, status=row.status, attempts=row.attempts, code=row.last_error)


def latest(session: Session, owner: str, kind: str) -> Status | None:
    row = session.scalars(select(EmailDelivery).where(EmailDelivery.owner == owner, EmailDelivery.kind == kind, EmailDelivery.status != "superseded")
                          .order_by(EmailDelivery.id.desc()).limit(1)).first()
    return _status(row) if row else None


def by_handle(session: Session, raw: str) -> EmailDelivery | None:
    if not raw or len(raw) > 100:
        return None
    return session.scalars(select(EmailDelivery).where(EmailDelivery.handle_hash == handle_digest(raw))).first()


def _status(row: EmailDelivery) -> Status:
    shown = "sent" if row.status == "sending" else row.status
    return Status(status=shown, reason=row.bounce_reason or row.last_error, recipient=row.recipient, sent_at=row.sent_at, created_at=row.created_at)


def retry_due(session: Session, *, limit: int = 25) -> dict[str, int]:
    """Worker (SYSTEM): send again what is due; returns how many went out and how many wait or failed."""
    from . import system_mail

    now = datetime.now(UTC)
    rows = session.scalars(select(EmailDelivery).where(EmailDelivery.status == "retrying", EmailDelivery.next_attempt_at <= now)
                           .order_by(EmailDelivery.next_attempt_at).limit(limit)).all()
    counts = {"sent": 0, "waiting": 0, "failed": 0}
    for row in rows:
        content = unseal(row.payload_encrypted)
        if content is None:
            row.status, row.settled_at, row.last_error, row.updated_at = "failed", now, "CONTENT_UNAVAILABLE", now
            counts["failed"] += 1
            continue
        try:
            system_mail.deliver(row.recipient, content["subject"], content["text"], content["html"], message_id=row.message_id)
            mark_sent(row)
            counts["sent"] += 1
            log_event(log, "e-mail sent on retry", kind=row.kind, attempts=row.attempts)
        except (TransientExternalError, PermanentExternalError, AppError) as exc:
            mark_failed(row, exc)
            counts["waiting" if row.status == "retrying" else "failed"] += 1
    return counts


def purge(session: Session, *, days: int = 90) -> int:
    """Retention: the record of an e-mail is kept 90 days."""
    cutoff = datetime.now(UTC) - timedelta(days=days)
    rows = session.scalars(select(EmailDelivery).where(EmailDelivery.created_at < cutoff)).all()
    for row in rows:
        session.delete(row)
    return len(rows)


def as_dict(row: EmailDelivery) -> dict[str, Any]:
    return {"id": row.id, "kind": row.kind, "owner": row.owner, "recipient": row.recipient, "status": row.status, "attempts": row.attempts,
            "last_error": row.last_error, "bounce_reason": row.bounce_reason, "bounce_detail": row.bounce_detail,
            "created_at": row.created_at, "sent_at": row.sent_at, "settled_at": row.settled_at}
