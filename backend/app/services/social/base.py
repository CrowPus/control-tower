"""What every network adapter (meta.py, linkedin.py, youtube.py) gives and takes."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path


@dataclass
class AccountInfo:
    network: str
    external_id: str
    name: str
    token: str
    username: str = ""
    picture_url: str = ""
    profile_url: str = ""
    refresh: str = ""
    expires_at: datetime | None = None
    followers: int | None = None
    # Instagram: the external id of the Facebook Page whose token publishes for it.
    parent_external_id: str = ""


@dataclass
class Picture:
    data: bytes
    mime_type: str
    url: str  # the public address (Meta fetches pictures from it)
    alt_text: str = ""


@dataclass
class Video:
    path: Path
    size: int
    url: str  # the public address while the post goes out (Meta fetches from it)
    mime_type: str = "video/mp4"


@dataclass
class Outgoing:
    kind: str  # text | image | carousel | video
    text: str
    link: str = ""
    title: str = ""
    pictures: list[Picture] = field(default_factory=list)
    video: Video | None = None
    ai_made: bool = False


@dataclass
class Published:
    external_id: str
    url: str
