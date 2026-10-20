"""Mail (Admin → Mail): the team's own e-mail program inside the admin app.

Accounts are ordinary mailboxes (IMAP to read, SMTP to send) the team adds like in Outlook; who may
read and send from each is in `mail_access`. The worker mirrors each account's folders and messages
(`mail_folders`, `mail_messages`) so lists and search are instant; reading, flags and moves go to the
server at once. What the team writes is a draft until it is sent (`mail_drafts`, files in
`mail_uploads`), then waits in the outbox for its undo window or the time it was scheduled for.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, IdMixin, UTCDateTime


class MailAccount(Base, IdMixin):
    __tablename__ = "mail_accounts"

    address: Mapped[str] = mapped_column(String(254), nullable=False)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    # A team mailbox from Admin → Team → Mailboxes ("marketing"): its server settings and password come
    # from there, so they are kept in one place. Empty: the settings below.
    team_mailbox: Mapped[str] = mapped_column(String(40), nullable=False, default="")
    login: Mapped[str] = mapped_column(String(254), nullable=False, default="")
    password_enc: Mapped[str] = mapped_column(Text, nullable=False, default="")
    imap_host: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    imap_port: Mapped[int] = mapped_column(Integer, nullable=False, default=993)
    imap_security: Mapped[str] = mapped_column(String(10), nullable=False, default="ssl")  # ssl | starttls
    smtp_host: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    smtp_port: Mapped[int] = mapped_column(Integer, nullable=False, default=465)
    smtp_security: Mapped[str] = mapped_column(String(10), nullable=False, default="ssl")  # ssl | starttls
    # Some providers (Gmail, Microsoft) file what is sent through SMTP themselves; the rest need a copy.
    save_sent_copy: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # The language of replies' quote lines and subject prefixes (AW:/WG: or Re:/Fwd:).
    language: Mapped[str] = mapped_column(String(5), nullable=False, default="de")
    color: Mapped[str] = mapped_column(String(20), nullable=False, default="")
    # How far back the first sync reaches (days).
    sync_days: Mapped[int] = mapped_column(Integer, nullable=False, default=90)
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="new")  # new | ok | error | paused
    last_error: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    last_sync_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    created_by_admin_id: Mapped[int | None] = mapped_column(ForeignKey("admin_users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class MailAccess(Base, IdMixin):
    """Who on the team may open an account, whether they may send from it, and their signature in it."""

    __tablename__ = "mail_access"
    __table_args__ = (UniqueConstraint("account_id", "admin_id", name="uq_mail_access"),)

    account_id: Mapped[int] = mapped_column(ForeignKey("mail_accounts.id", ondelete="CASCADE"), nullable=False)
    admin_id: Mapped[int] = mapped_column(ForeignKey("admin_users.id", ondelete="CASCADE"), nullable=False)
    can_send: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # May change the account's settings and who has access (the one who added it, or an owner's grant).
    can_manage: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    signature_html: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class MailFolder(Base, IdMixin):
    __tablename__ = "mail_folders"
    __table_args__ = (UniqueConstraint("account_id", "name", name="uq_mail_folder"),)

    account_id: Mapped[int] = mapped_column(ForeignKey("mail_accounts.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String(500), nullable=False)  # as the server names it (modified UTF-7)
    display_name: Mapped[str] = mapped_column(String(500), nullable=False)
    delimiter: Mapped[str] = mapped_column(String(4), nullable=False, default="/")
    role: Mapped[str] = mapped_column(String(10), nullable=False, default="")  # inbox | sent | drafts | trash | junk | archive | ""
    uid_validity: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Highest UID mirrored; and the lowest, so older mail can be fetched on request.
    last_uid: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    first_uid: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Everything older than first_uid is on the server only (until "load older").
    has_older: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # The first sync reached back the account's sync days (the rest is fetched on request).
    window_done: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    unread: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    synced_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class MailMessage(Base, IdMixin):
    __tablename__ = "mail_messages"
    __table_args__ = (
        UniqueConstraint("folder_id", "uid", name="uq_mail_message_uid"),
        Index("ix_mail_messages_folder_date", "folder_id", "received_at"),
        Index("ix_mail_messages_account_mid", "account_id", "message_id"),
        Index("ix_mail_messages_thread", "account_id", "thread_key"),
    )

    account_id: Mapped[int] = mapped_column(ForeignKey("mail_accounts.id", ondelete="CASCADE"), nullable=False)
    folder_id: Mapped[int] = mapped_column(ForeignKey("mail_folders.id", ondelete="CASCADE"), nullable=False)
    uid: Mapped[int] = mapped_column(Integer, nullable=False)
    message_id: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    in_reply_to: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    references: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # The conversation: the Message-ID of its first message we know of.
    thread_key: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    subject: Mapped[str] = mapped_column(String(1000), nullable=False, default="")
    from_name: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    from_address: Mapped[str] = mapped_column(String(320), nullable=False, default="")
    # [[name, address], ...]
    to: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)
    cc: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)
    bcc: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)
    reply_to: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)
    sent_at: Mapped[datetime | None] = mapped_column(UTCDateTime())  # the Date header
    received_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)  # the server's arrival time
    size: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    seen: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    flagged: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    answered: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    forwarded: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_draft: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    has_attachments: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # [{"index": n, "filename", "mime", "size", "cid", "inline"}]: the parts themselves stay on the server.
    attachments: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)
    preview: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    html: Mapped[str] = mapped_column(Text, nullable=False, default="")  # sanitized when stored
    body_state: Mapped[str] = mapped_column(String(10), nullable=False, default="full")  # full | too_large
    list_unsubscribe: Mapped[str] = mapped_column(String(1000), nullable=False, default="")
    # Our own draft this server copy is of (the X-Tower-Draft header).
    draft_ref: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class MailDraft(Base, IdMixin):
    __tablename__ = "mail_drafts"
    __table_args__ = (Index("ix_mail_drafts_admin", "admin_id", "updated_at"),)

    account_id: Mapped[int] = mapped_column(ForeignKey("mail_accounts.id", ondelete="CASCADE"), nullable=False)
    admin_id: Mapped[int] = mapped_column(ForeignKey("admin_users.id", ondelete="CASCADE"), nullable=False)
    mode: Mapped[str] = mapped_column(String(10), nullable=False, default="new")  # new | reply | reply_all | forward
    source_message_id: Mapped[int | None] = mapped_column(ForeignKey("mail_messages.id", ondelete="SET NULL"))
    to: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)  # ["Name <a@b>", ...]
    cc: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)
    bcc: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)
    subject: Mapped[str] = mapped_column(String(1000), nullable=False, default="")
    html: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # The thread it answers (In-Reply-To and References of the mail as it goes out).
    in_reply_to: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    references: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # The copy in the account's Drafts folder (so it is on the phone too), replaced when it changes.
    server_uid: Mapped[int | None] = mapped_column(Integer)
    server_folder_id: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class MailUpload(Base, IdMixin):
    """A file attached to a draft (and then to the mail in the outbox)."""

    __tablename__ = "mail_uploads"
    __table_args__ = (Index("ix_mail_uploads_draft", "draft_id"),)

    admin_id: Mapped[int] = mapped_column(ForeignKey("admin_users.id", ondelete="CASCADE"), nullable=False)
    draft_id: Mapped[int | None] = mapped_column(ForeignKey("mail_drafts.id", ondelete="SET NULL"))
    outbox_id: Mapped[int | None] = mapped_column(ForeignKey("mail_outbox.id", ondelete="SET NULL"))
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(120), nullable=False)
    size: Mapped[int] = mapped_column(Integer, nullable=False)
    data: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    # An image placed in the text (signature logo, pasted picture): referenced as cid:<content_id>.
    content_id: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    scan: Mapped[str] = mapped_column(String(20), nullable=False, default="off")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class MailOutbox(Base, IdMixin):
    __tablename__ = "mail_outbox"
    __table_args__ = (Index("ix_mail_outbox_status", "status", "send_at"),)

    account_id: Mapped[int] = mapped_column(ForeignKey("mail_accounts.id", ondelete="CASCADE"), nullable=False)
    admin_id: Mapped[int | None] = mapped_column(ForeignKey("admin_users.id", ondelete="SET NULL"))
    mode: Mapped[str] = mapped_column(String(10), nullable=False, default="new")
    source_message_id: Mapped[int | None] = mapped_column(ForeignKey("mail_messages.id", ondelete="SET NULL"))
    to: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)
    cc: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)
    bcc: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)
    subject: Mapped[str] = mapped_column(String(1000), nullable=False, default="")
    html: Mapped[str] = mapped_column(Text, nullable=False, default="")
    in_reply_to: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    references: Mapped[str] = mapped_column(Text, nullable=False, default="")
    send_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="queued")  # queued | sending | sent | failed | cancelled
    error: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    message_id: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    sent_folder: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    sent_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    # The draft it came from, kept until it is sent so "Undo" brings it back as it was.
    draft_server_uid: Mapped[int | None] = mapped_column(Integer)
    draft_server_folder_id: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
