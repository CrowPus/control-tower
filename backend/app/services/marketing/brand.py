"""The brand kit and the owner's marketing settings (Admin → Marketing → Brand): what the company is, how it looks and
sounds, and the numbers the team works within. All of it is the owner's; nothing here is assumed."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from .. import platform


class BrandKit(BaseModel):
    # The company's name (empty: the install's APP_NAME) and, in the owner's words, what it does and for whom: every
    # agent reads it before it works.
    name: str = Field(default="", max_length=80)
    tagline: str = Field(default="", max_length=200)
    about: str = Field(default="", max_length=2000)
    # How the name is said, when a video model might get it wrong (e.g. "EP-liv"); empty: no hint.
    pronunciation: str = Field(default="", max_length=60)
    # Colours as #RRGGBB: the band behind a picture's headline, its text, and the accent line.
    primary_color: str = Field(default="#0F6B5C", pattern=r"^#[0-9A-Fa-f]{6}$")
    text_color: str = Field(default="#FFFFFF", pattern=r"^#[0-9A-Fa-f]{6}$")
    accent_color: str = Field(default="#F59E0B", pattern=r"^#[0-9A-Fa-f]{6}$")
    # The logo put on pictures (a picture in the media library), and where.
    logo_media_id: int | None = None
    logo_position: Literal["top_left", "top_right", "bottom_left", "bottom_right", "none"] = "top_left"
    # How the company sounds, the style of its pictures, words to use and to avoid, hashtags it uses.
    voice: str = Field(default="", max_length=3000)
    image_style: str = Field(default="", max_length=2000)
    words_to_use: list[str] = Field(default_factory=list, max_length=50)
    words_to_avoid: list[str] = Field(default_factory=list, max_length=50)
    hashtags: list[str] = Field(default_factory=list, max_length=30)
    # Languages posts are written in, besides English (the customers' languages, e.g. fr, pt, es, ar).
    languages: list[str] = Field(default_factory=lambda: ["en"], max_length=10)
    # Written on every AI-made picture (the owner's decision: AI people allowed, always labelled).
    ai_label: str = Field(default="AI-generated", max_length=40)
    # What the company offers its customers, as the owner approved it (the website's wording): the only things videos and
    # posts may say about the company.
    facts_for_customers: list[str] = Field(default_factory=list, max_length=40)


class MarketingSettings(BaseModel):
    # What pictures may cost per month in USD; empty: none are made until the owner sets it.
    monthly_picture_budget_usd: float | None = Field(default=None, ge=0, le=10000)
    picture_quality: Literal["standard", "best"] = "standard"
    # At most this many posts per network and day are proposed (empty: the agents' own judgement, at most 2).
    max_posts_per_network_per_day: int | None = Field(default=None, ge=0, le=20)
    # Hours (the owner's time zone) an approved post without its own time goes out, e.g. [9, 12, 18].
    posting_hours: list[int] = Field(default_factory=list, max_length=12)
    # Videos: what they may cost per month in USD (empty: none are made), the model's
    # quality (fast or best), and how many videos per day or week are made and posted (empty: none are proposed).
    monthly_video_budget_usd: float | None = Field(default=None, ge=0, le=10000)
    video_quality: Literal["fast", "best"] = "fast"
    videos_count: int | None = Field(default=None, ge=0, le=20)
    videos_per: Literal["day", "week"] = "week"


def kit(session: Session) -> BrandKit:
    value = BrandKit.model_validate(platform.get(session, "brand") or {})
    if not value.name:
        from ...config import settings

        value.name = settings().app_name
    return value


def company(session: Session) -> tuple[str, str]:
    """The company's name and what it does (the owner's words; empty when not written yet)."""
    value = kit(session)
    return value.name, value.about


def save_kit(session: Session, value: BrandKit) -> None:
    platform.put(session, "brand", value.model_dump())


def settings_of(session: Session) -> MarketingSettings:
    return MarketingSettings.model_validate(platform.get(session, "marketing") or {})


def save_settings(session: Session, value: MarketingSettings) -> None:
    platform.put(session, "marketing", value.model_dump())
