"""Social media: the accounts the company connected (Facebook Page, Instagram,
LinkedIn company page, YouTube channel, TikTok), the pictures the team made or uploaded, the videos the
Graphic Designer made from approved concepts, the Marketing Lead's
campaign plans, and every post — written by an agent, approved by the owner, published by the service
(or by hand where a network is not connected) — with what it achieved."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Float,
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

# Networks a post can go to. facebook/instagram/linkedin/youtube/tiktok are published by the service once
# connected; x is posted by hand.
NETWORKS = ("facebook", "instagram", "linkedin", "youtube", "tiktok", "x")


class SocialAccount(Base, IdMixin):
    """A connected account: a Facebook Page, the Instagram business account linked to it, a LinkedIn
    company page or a YouTube channel. Its tokens are stored encrypted and never shown."""

    __tablename__ = "social_accounts"
    __table_args__ = (UniqueConstraint("network", "external_id", name="uq_social_account"),)

    network: Mapped[str] = mapped_column(String(12), nullable=False)
    external_id: Mapped[str] = mapped_column(String(120), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    username: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    picture_url: Mapped[str] = mapped_column(String(1000), nullable=False, default="")
    profile_url: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    token_enc: Mapped[str] = mapped_column(Text, nullable=False, default="")
    refresh_enc: Mapped[str] = mapped_column(Text, nullable=False, default="")
    token_expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    # Instagram: the Facebook Page it is linked to (its token publishes for it).
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("social_accounts.id", ondelete="CASCADE"))
    status: Mapped[str] = mapped_column(String(12), nullable=False, default="ok")  # ok | error | off
    last_error: Mapped[str] = mapped_column(String(400), nullable=False, default="")
    followers: Mapped[int | None] = mapped_column(Integer)
    followers_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    connected_by_admin_id: Mapped[int | None] = mapped_column(ForeignKey("admin_users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class MarketingMedia(Base, IdMixin):
    """A picture (made by the studio or uploaded by the owner) or a video the owner made and linked."""

    __tablename__ = "marketing_media"
    __table_args__ = (UniqueConstraint("public_token", name="uq_marketing_media_token"),)

    kind: Mapped[str] = mapped_column(String(8), nullable=False)  # image | video
    source: Mapped[str] = mapped_column(String(10), nullable=False)  # generated | upload | link | storyboard (a video scene's first frame)
    title: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    prompt: Mapped[str] = mapped_column(Text, nullable=False, default="")
    model: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    mime_type: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    width: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    height: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    size: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    data: Mapped[bytes | None] = mapped_column(LargeBinary)
    # A video the owner made: where the file is (fetched when it is posted, never kept).
    link: Mapped[str] = mapped_column(String(1000), nullable=False, default="")
    # A video the studio made: its file name in the marketing folder (settings.marketing_dir); `data` holds its cover picture.
    file: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    seconds: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    # The networks need a public address for a picture; unguessable, one per file.
    public_token: Mapped[str] = mapped_column(String(64), nullable=False)
    ai_generated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    shows_people: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    alt_text: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    cost_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    created_by: Mapped[str] = mapped_column(String(60), nullable=False, default="")  # an agent's key, or admin:<id>
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class MarketingPlan(Base, IdMixin):
    """The Marketing Lead's campaign plan for a period, as the owner approved it; its briefs go to the
    specialists (Social Media, Candidate Acquisition, Outreach)."""

    __tablename__ = "marketing_plans"
    __table_args__ = (Index("ix_marketing_plans_status", "status", "period_start"),)

    title: Mapped[str] = mapped_column(String(200), nullable=False)
    period_start: Mapped[str] = mapped_column(String(10), nullable=False)  # YYYY-MM-DD
    period_end: Mapped[str] = mapped_column(String(10), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # {"goals": [...], "audiences": [...], "campaigns": [...], "briefs": {"agent_key": "..."}, "measures": [...]}
    body: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="active")  # active | replaced | done
    approval_id: Mapped[int | None] = mapped_column(ForeignKey("team_approvals.id", ondelete="SET NULL"))
    agent: Mapped[str] = mapped_column(String(40), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class SocialPost(Base, IdMixin):
    """One post on one network. Approved posts are published by the service at their time on a connected
    network, or wait to be posted by hand; a video post waits for the owner's video first."""

    __tablename__ = "social_posts"
    __table_args__ = (Index("ix_social_posts_status", "status", "id"), Index("ix_social_posts_scheduled", "status", "scheduled_at"))

    network: Mapped[str] = mapped_column(String(12), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    link: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    image_note: Mapped[str] = mapped_column(Text, nullable=False, default="")
    agent: Mapped[str] = mapped_column(String(40), nullable=False, default="")
    approval_id: Mapped[int | None] = mapped_column(ForeignKey("team_approvals.id", ondelete="SET NULL"))
    # ready (by hand) | needs_video | scheduled | publishing | posted | failed | discarded
    status: Mapped[str] = mapped_column(String(12), nullable=False, default="ready")
    post_url: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    posted_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    kind: Mapped[str] = mapped_column(String(10), nullable=False, default="text")  # text | image | carousel | video
    title: Mapped[str] = mapped_column(String(200), nullable=False, default="")  # YouTube
    media_ids: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)
    # A video post: the script the owner films from ({"hook", "scenes": [...], "voiceover", "on_screen_text", "length_seconds", "music"}).
    video_script: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    account_id: Mapped[int | None] = mapped_column(ForeignKey("social_accounts.id", ondelete="SET NULL"))
    scheduled_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    external_id: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    error: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    # {"reach", "views", "likes", "comments", "shares", "saves", "clicks"} as each network reports them.
    metrics: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    metrics_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    plan_id: Mapped[int | None] = mapped_column(ForeignKey("marketing_plans.id", ondelete="SET NULL"))


class MarketingVideo(Base, IdMixin):
    """A video the Graphic Designer conceived and the owner approved: its concept,
    each scene's generation (started, waiting, made), the finished file (a MarketingMedia video) and what it cost.
    The owner deletes the file once it is posted; the concept stays for the record."""

    __tablename__ = "marketing_videos"
    __table_args__ = (Index("ix_marketing_videos_status", "status", "id"),)

    title: Mapped[str] = mapped_column(String(200), nullable=False)
    # The approved concept: idea, hook, scenes (shot, action, dialogue, sound, on-screen text), end card, caption, networks.
    concept: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    # making | ready | failed | deleted
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="making")
    # Per scene: {"operation", "state": "start|wait|done|failed", "file", "started_at", "error", "cost_usd"}.
    progress: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    model: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    uses_character: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    media_id: Mapped[int | None] = mapped_column(ForeignKey("marketing_media.id", ondelete="SET NULL"))
    cost_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    error: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    approval_id: Mapped[int | None] = mapped_column(ForeignKey("team_approvals.id", ondelete="SET NULL"))
    agent: Mapped[str] = mapped_column(String(40), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    made_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    deleted_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
