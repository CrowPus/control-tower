"""Public addresses of the studio's pictures and videos: Instagram, Facebook and the other networks fetch media from an
address while a post goes out. Each address carries an unguessable token; nothing else is reachable here, and these
addresses are open to the internet (outside the admin's IP allowlist)."""

from __future__ import annotations

import re

from fastapi import APIRouter
from fastapi.responses import FileResponse, Response
from sqlalchemy import select

from .core.errors import NotFound
from .db import transaction
from .models import MarketingMedia
from .services.marketing import video_studio
from .services.social import video

router = APIRouter(include_in_schema=False)
_TOKEN = re.compile(r"^[A-Za-z0-9_-]{20,64}$")
PREFIX = "/api/v1/media/"


def _missing() -> NotFound:
    return NotFound("Not found.", code="NOT_FOUND")


@router.get(PREFIX + "tmp/{name}")
def temporary_video(name: str) -> FileResponse:
    token = name.removesuffix(".mp4")
    path = video.temp_file(token) if _TOKEN.match(token) else None
    if path is None:
        raise _missing()
    return FileResponse(path, media_type="video/mp4", headers={"Cache-Control": "no-store", "X-Robots-Tag": "noindex"})


@router.get(PREFIX + "{name}")
def media(name: str) -> Response:
    if name.endswith(".mp4"):
        token = name.removesuffix(".mp4")
        if not _TOKEN.match(token):
            raise _missing()
        with transaction() as session:
            row = session.execute(select(MarketingMedia).where(MarketingMedia.public_token == token, MarketingMedia.kind == "video")).scalar_one_or_none()
            path = video_studio.file_of(row) if row is not None else None
        if path is None:
            raise _missing()
        return FileResponse(path, media_type="video/mp4", headers={"Cache-Control": "public, max-age=3600", "X-Robots-Tag": "noindex", "X-Content-Type-Options": "nosniff"})
    token = re.sub(r"\.(jpg|jpeg|png)$", "", name)
    if not _TOKEN.match(token):
        raise _missing()
    with transaction() as session:
        picture = session.execute(select(MarketingMedia.data, MarketingMedia.mime_type).where(MarketingMedia.public_token == token, MarketingMedia.kind == "image")).first()
    if picture is None or not picture.data:
        raise _missing()
    return Response(content=picture.data, media_type=picture.mime_type, headers={"Cache-Control": "public, max-age=86400", "X-Robots-Tag": "noindex",
                                                                                  "Content-Security-Policy": "default-src 'none'", "X-Content-Type-Options": "nosniff"})
