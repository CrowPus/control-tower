"""Every e-mail the service sends, and what became of it (services/mail_delivery.py).

A server table: no member or company owns a row (`owner` names whose e-mail it
is, e.g. "user:12", "employer_user:3"), so it is read only through the code that
checks that owner. The content is kept encrypted only until the e-mail is settled
(sent, given up or returned): long enough to retry, never longer.
"""

from datetime import datetime

from sqlalchemy import Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, IdMixin, UTCDateTime


class EmailDelivery(Base, IdMixin):
    __tablename__ = "email_deliveries"
    __table_args__ = (
        Index("ix_email_deliveries_owner_kind", "owner", "kind", "created_at"),
        Index("ix_email_deliveries_status_next", "status", "next_attempt_at"),
        Index("ix_email_deliveries_message_id", "message_id"),
        Index("ix_email_deliveries_handle", "handle_hash"),
    )

    kind: Mapped[str] = mapped_column(String(60), nullable=False)
    owner: Mapped[str] = mapped_column(String(60), nullable=False, default="")
    recipient: Mapped[str] = mapped_column(String(320), nullable=False)
    language: Mapped[str] = mapped_column(String(5), nullable=False, default="de")
    message_id: Mapped[str] = mapped_column(String(250), nullable=False, default="")
    # sending → sent | retrying → sent | failed; sent → bounced; superseded by a newer one of the same kind.
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="sending")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_attempt_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    last_error: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    # Why it came back: no_such_user | mailbox_full | blocked | rejected.
    bounce_reason: Mapped[str] = mapped_column(String(30), nullable=False, default="")
    bounce_detail: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    # A private reference for a page that has no sign-in (employer registration), hashed.
    handle_hash: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    payload_encrypted: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    sent_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    settled_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class MailboxCursor(Base, IdMixin):
    """How far the returned-mail check has read the service mailbox."""

    __tablename__ = "mailbox_cursors"

    mailbox: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    uid_validity: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_uid: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    scanned_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
