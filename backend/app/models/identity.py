from datetime import datetime

from sqlalchemy import Boolean, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, IdMixin, UTCDateTime


class LoginAttempt(Base, IdMixin):
    """Per-IP login throttling that survives process restarts."""

    __tablename__ = "login_attempts"
    __table_args__ = (Index("ix_login_attempts_key_at", "key", "attempted_at"),)

    key: Mapped[str] = mapped_column(String(200), nullable=False)  # ip or ip+email
    attempted_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    success: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
