"""A YouTube channel through the YouTube Data API (a Google Cloud app in the owner's name). Videos the
owner made go up as Shorts (vertical, up to 3 minutes). Until Google verifies the app, YouTube keeps
uploads private; the owner then makes them public in YouTube Studio."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode

from ...config import settings
from . import net
from .base import AccountInfo, Outgoing, Published

NAME = "YouTube"
SCOPES = ("https://www.googleapis.com/auth/youtube.upload", "https://www.googleapis.com/auth/youtube.readonly")
API = "https://www.googleapis.com/youtube/v3"


def authorize_url(client_id: str, redirect_uri: str, state: str) -> str:
    query = {"client_id": client_id, "redirect_uri": redirect_uri, "response_type": "code", "scope": " ".join(SCOPES), "access_type": "offline",
             "prompt": "consent", "include_granted_scopes": "true", "state": state}
    return f"https://accounts.google.com/o/oauth2/v2/auth?{urlencode(query)}"


def _tokens(body: dict[str, Any]) -> tuple[str, str, datetime | None]:
    expires = datetime.now(UTC) + timedelta(seconds=int(body.get("expires_in") or 0)) if body.get("expires_in") else None
    return str(body.get("access_token") or ""), str(body.get("refresh_token") or ""), expires


def connect(code: str, client_id: str, secret: str, redirect_uri: str) -> list[AccountInfo]:
    with net.client() as http:
        body = net.check(http.post("https://oauth2.googleapis.com/token", data={"code": code, "client_id": client_id, "client_secret": secret, "redirect_uri": redirect_uri,
                                                                                "grant_type": "authorization_code"}), NAME)
        token, refresh, expires = _tokens(body)
        channels = net.check(http.get(f"{API}/channels", params={"part": "snippet,statistics", "mine": "true"}, headers={"Authorization": f"Bearer {token}"}), NAME)
    out = []
    for item in channels.get("items") or []:
        snippet, stats = item.get("snippet") or {}, item.get("statistics") or {}
        handle = str(snippet.get("customUrl") or "")
        out.append(AccountInfo(network="youtube", external_id=str(item["id"]), name=str(snippet.get("title") or ""), token=token, refresh=refresh, expires_at=expires,
                               username=handle, picture_url=str(((snippet.get("thumbnails") or {}).get("default") or {}).get("url") or ""),
                               profile_url=f"https://www.youtube.com/{handle}" if handle else f"https://www.youtube.com/channel/{item['id']}",
                               followers=int(stats["subscriberCount"]) if stats.get("subscriberCount") else None))
    if not out:
        raise net.NetworkError("This Google account has no YouTube channel. Sign in with the account that owns the company's channel.")
    return out


def refresh_token(refresh: str, client_id: str, secret: str) -> tuple[str, str, datetime | None]:
    with net.client() as http:
        body = net.check(http.post("https://oauth2.googleapis.com/token", data={"refresh_token": refresh, "client_id": client_id, "client_secret": secret,
                                                                                "grant_type": "refresh_token"}), NAME)
    token, new_refresh, expires = _tokens(body)
    return token, new_refresh or refresh, expires


def publish(token: str, post: Outgoing) -> Published:
    if post.kind != "video" or post.video is None:
        raise net.NetworkError("YouTube takes videos only.")
    first_line = post.text.strip().splitlines()[0] if post.text.strip() else ""
    title = (post.title or first_line or settings().app_name)[:95]
    if "#shorts" not in title.lower() and "#shorts" not in post.text.lower():
        description = f"{post.text}\n\n#Shorts"
    else:
        description = post.text
    if post.link and post.link not in description:
        description = f"{description}\n\n{post.link}"
    meta = {"snippet": {"title": title, "description": description[:4900], "categoryId": "22", "defaultLanguage": "de"},
            "status": {"privacyStatus": "public", "selfDeclaredMadeForKids": False, "containsSyntheticMedia": post.ai_made}}
    with net.client(timeout=1800) as http:
        start = http.post("https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&part=snippet,status", json=meta,
                          headers={"Authorization": f"Bearer {token}", "X-Upload-Content-Type": post.video.mime_type, "X-Upload-Content-Length": str(post.video.size)})
        net.check(start, NAME)
        location = start.headers.get("location", "")
        if not location:
            raise net.NetworkError("YouTube did not accept the upload.")
        with post.video.path.open("rb") as file:
            uploaded = net.check(http.put(location, content=file, headers={"Content-Type": post.video.mime_type, "Content-Length": str(post.video.size)}), NAME)
    video_id = str(uploaded.get("id") or "")
    return Published(external_id=video_id, url=f"https://www.youtube.com/shorts/{video_id}")


def metrics(token: str, video_id: str) -> dict[str, int]:
    with net.client() as http:
        response = http.get(f"{API}/videos", params={"part": "statistics", "id": video_id}, headers={"Authorization": f"Bearer {token}"})
    if response.status_code >= 400:
        return {}
    items = response.json().get("items") or []
    stats = items[0].get("statistics") if items else {}
    return {"views": int(stats.get("viewCount") or 0), "likes": int(stats.get("likeCount") or 0), "comments": int(stats.get("commentCount") or 0)}


def followers(token: str) -> int | None:
    with net.client() as http:
        response = http.get(f"{API}/channels", params={"part": "statistics", "mine": "true"}, headers={"Authorization": f"Bearer {token}"})
    if response.status_code >= 400:
        return None
    items = response.json().get("items") or []
    count = (items[0].get("statistics") or {}).get("subscriberCount") if items else None
    return int(count) if count else None
