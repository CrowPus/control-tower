"""Tables for running an install: its settings and the admin side (accounts,
sessions, the admin log)."""

from datetime import datetime
from typing import Any

from sqlalchemy import ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from .base import Base, IdMixin, TimestampMixin, UTCDateTime


class PlatformSetting(Base, IdMixin, TimestampMixin):
    """Install-wide settings the admin changes (the kill switches, the agents'
    settings, every business number). One row per key."""

    __tablename__ = "platform_settings"

    key: Mapped[str] = mapped_column(String(80), nullable=False, unique=True)
    value: Mapped[Any] = mapped_column(JSON, nullable=False)


class AdminUser(Base, IdMixin, TimestampMixin):
    __tablename__ = "admin_users"

    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True)
    full_name: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    role: Mapped[str] = mapped_column(String(20), nullable=False, default="support")  # owner | support
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="invited")  # invited | active | disabled
    # Authenticator-app secret, AEAD-encrypted like integration credentials.
    totp_secret_ciphertext: Mapped[str] = mapped_column(Text, nullable=False, default="")
    totp_enabled_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    # The last accepted code's time step: a code is accepted once.
    totp_last_step: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    invite_token_hash: Mapped[str | None] = mapped_column(String(64), unique=True)
    invite_expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    invited_by_admin_id: Mapped[int | None] = mapped_column(ForeignKey("admin_users.id", ondelete="SET NULL"))
    last_login_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    failed_login_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(UTCDateTime())
    # The bell: events after this moment are new to this admin.
    notifications_seen_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class AdminSession(Base, IdMixin):
    __tablename__ = "admin_sessions"
    __table_args__ = (Index("ix_admin_sessions_admin", "admin_user_id", "expires_at"),)

    admin_user_id: Mapped[int] = mapped_column(ForeignKey("admin_users.id", ondelete="CASCADE"), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    csrf_token: Mapped[str] = mapped_column(String(64), nullable=False)
    # Password accepted but the authenticator code not yet: such a session can do
    # nothing except finish sign-in (or enrol, the first time).
    mfa_passed: Mapped[bool] = mapped_column(nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    ip_address: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    user_agent: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class AdminAuditLog(Base, IdMixin):
    """Every admin action. Append-only: the admin database role may insert and
    read, never update or delete."""

    __tablename__ = "admin_audit_logs"
    __table_args__ = (Index("ix_admin_audit_logs_at", "timestamp"),)

    timestamp: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    admin_user_id: Mapped[int | None] = mapped_column(Integer)
    admin_email: Mapped[str] = mapped_column(String(320), nullable=False, default="")
    action: Mapped[str] = mapped_column(String(120), nullable=False)
    target_type: Mapped[str] = mapped_column(String(60), nullable=False, default="")
    target_id: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    ip_address: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    result: Mapped[str] = mapped_column(String(30), nullable=False, default="ok")
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, nullable=False, default=dict)
