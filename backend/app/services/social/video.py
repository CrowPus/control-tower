"""Videos the owner made and linked: the link is turned into the file's own
address, the file is fetched only when the post goes out (into the shared data folder, where the
networks that need an address can fetch it), and deleted right after."""

from __future__ import annotations

import os
import re
import secrets
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from ...config import settings
from . import net

# Safety rail, not a business value: the largest video fetched (the networks take up to about 1 GB; Reels 15 min).
MAX_BYTES = 1024 * 1024 * 1024


def direct_link(link: str) -> str:
    link = (link or "").strip()
    if not re.match(r"^https://[^\s\"'<>]+$", link):
        return ""
    drive = re.match(r"^https://drive\.google\.com/file/d/([A-Za-z0-9_-]+)", link) or re.search(r"drive\.google\.com/open\?id=([A-Za-z0-9_-]+)", link)
    if drive:
        return f"https://drive.google.com/uc?export=download&confirm=t&id={drive.group(1)}"
    if "dropbox.com/" in link:
        return re.sub(r"([?&])dl=0", r"\1dl=1", link) if "dl=0" in link else link + ("&" if "?" in link else "?") + "dl=1"
    return link


@dataclass
class Fetched:
    path: Path
    token: str
    size: int
    mime_type: str

    def public_url(self) -> str:
        base = (settings().public_base_url or "").rstrip("/")
        return f"{base}/api/v1/media/tmp/{self.token}.mp4" if base else ""


def folder() -> Path:
    path = Path(settings().data_dir) / "marketing" / "tmp"
    path.mkdir(parents=True, exist_ok=True)
    return path


@contextmanager
def fetched(link: str) -> Iterator[Fetched]:
    """The video file, for as long as the post takes; removed afterwards."""
    token = secrets.token_urlsafe(24)
    path = folder() / f"{token}.mp4"
    size = 0
    mime = "video/mp4"
    try:
        with net.client(timeout=600) as http, http.stream("GET", link) as response:
            if response.status_code >= 400:
                raise net.NetworkError(f"The video link answered {response.status_code}; is it shared for everyone with the link?")
            mime = (response.headers.get("content-type") or "video/mp4").split(";")[0].strip()
            if mime.startswith("text/html"):
                raise net.NetworkError("The video link opens a web page, not the file. Share it as a direct download (Google Drive: 'Anyone with the link').")
            with path.open("wb") as out:
                for chunk in response.iter_bytes(1024 * 1024):
                    size += len(chunk)
                    if size > MAX_BYTES:
                        raise net.NetworkError("The video is larger than 1 GB.")
                    out.write(chunk)
        if size < 10_000:
            raise net.NetworkError("The video link gave no video file.")
        yield Fetched(path=path, token=token, size=size, mime_type=mime if mime.startswith("video/") else "video/mp4")
    finally:
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass


def temp_file(token: str) -> Path | None:
    if not re.match(r"^[A-Za-z0-9_-]{20,64}$", token):
        return None
    path = folder() / f"{token}.mp4"
    return path if path.exists() else None
