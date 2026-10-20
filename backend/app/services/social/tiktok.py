"""A TikTok account through TikTok's Content Posting API (an app in the owner's name at developers.tiktok.com,
with Login Kit and Content Posting API "Direct Post"). Videos are uploaded as files (no address to verify)
and marked as AI-made when they are. Until TikTok audits the app, TikTok lets it post only privately
(SELF_ONLY): the owner then makes the video public in the TikTok app.

Access tokens last a day and are renewed with the refresh token (a year)."""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode

from . import net
from .base import AccountInfo, Outgoing, Published

NAME = "TikTok"
API = "https://open.tiktokapis.com/v2"
SCOPES = ("user.info.basic", "user.info.profile", "user.info.stats", "video.publish", "video.list")
# TikTok's upload rules: one piece up to 64 MB, otherwise pieces of at least 5 MB (the last one takes the rest).
WHOLE_UP_TO = 64 * 1024 * 1024
PIECE = 10 * 1024 * 1024
# How long TikTok may take to process an upload before it is given up.
PREPARE_SECONDS = 600


def authorize_url(client_key: str, redirect_uri: str, state: str) -> str:
    query = {"client_key": client_key, "redirect_uri": redirect_uri, "state": state, "response_type": "code", "scope": ",".join(SCOPES)}
    return f"https://www.tiktok.com/v2/auth/authorize/?{urlencode(query)}"


def _body(response: Any) -> dict[str, Any]:
    body = net.check(response, NAME)
    error = body.get("error") if isinstance(body, dict) else None
    if isinstance(error, dict) and error.get("code") not in (None, "", "ok"):
        raise net.NetworkError(f"{NAME}: {error.get('message') or error.get('code')}"[:400])
    return body if isinstance(body, dict) else {}


def _tokens(body: dict[str, Any]) -> tuple[str, str, datetime | None]:
    expires = datetime.now(UTC) + timedelta(seconds=int(body.get("expires_in") or 0)) if body.get("expires_in") else None
    return str(body.get("access_token") or ""), str(body.get("refresh_token") or ""), expires


def _user(http: Any, token: str) -> dict[str, Any]:
    fields = "open_id,union_id,avatar_url,display_name,username,profile_deep_link,follower_count"
    body = _body(http.get(f"{API}/user/info/", params={"fields": fields}, headers={"Authorization": f"Bearer {token}"}))
    return (body.get("data") or {}).get("user") or {}


def connect(code: str, client_key: str, secret: str, redirect_uri: str) -> list[AccountInfo]:
    with net.client() as http:
        body = net.check(http.post(f"{API}/oauth/token/", data={"client_key": client_key, "client_secret": secret, "code": code, "grant_type": "authorization_code",
                                                                 "redirect_uri": redirect_uri}), NAME)
        if body.get("error"):
            raise net.NetworkError(f"{NAME}: {body.get('error_description') or body.get('error')}"[:400])
        token, refresh, expires = _tokens(body)
        user = _user(http, token)
    open_id = str(body.get("open_id") or user.get("open_id") or "")
    if not open_id:
        raise net.NetworkError("TikTok gave no account.")
    username = str(user.get("username") or "")
    return [AccountInfo(network="tiktok", external_id=open_id, name=str(user.get("display_name") or username or "TikTok"), token=token, refresh=refresh,
                        expires_at=expires, username=username, picture_url=str(user.get("avatar_url") or ""),
                        profile_url=str(user.get("profile_deep_link") or (f"https://www.tiktok.com/@{username}" if username else "")),
                        followers=user.get("follower_count"))]


def refresh_token(refresh: str, client_key: str, secret: str) -> tuple[str, str, datetime | None]:
    with net.client() as http:
        body = net.check(http.post(f"{API}/oauth/token/", data={"client_key": client_key, "client_secret": secret, "grant_type": "refresh_token",
                                                                 "refresh_token": refresh}), NAME)
    if body.get("error"):
        raise net.NetworkError(f"The TikTok sign-in ran out; the owner needs to press Connect again ({body.get('error_description') or body.get('error')}).")
    token, new_refresh, expires = _tokens(body)
    return token, new_refresh or refresh, expires


def _pieces(size: int) -> tuple[int, int]:
    """(piece size, number of pieces) as TikTok wants them."""
    if size <= WHOLE_UP_TO:
        return size, 1
    count = size // PIECE
    return PIECE, count


def publish(token: str, post: Outgoing) -> Published:
    if post.kind != "video" or post.video is None:
        raise net.NetworkError("TikTok takes videos only.")
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=UTF-8"}
    caption = post.text if not post.link or post.link in post.text else f"{post.text}\n\n{post.link}"
    size = post.video.size
    piece, count = _pieces(size)
    with net.client(timeout=1800) as http:
        creator = (_body(http.post(f"{API}/post/publish/creator_info/query/", headers=headers)).get("data") or {})
        options = [str(o) for o in creator.get("privacy_level_options") or []]
        privacy = "PUBLIC_TO_EVERYONE" if "PUBLIC_TO_EVERYONE" in options else (options[0] if options else "SELF_ONLY")
        started = _body(http.post(f"{API}/post/publish/video/init/", headers=headers, json={
            "post_info": {"title": caption[:2200], "privacy_level": privacy, "disable_duet": False, "disable_stitch": False, "disable_comment": False,
                          "video_cover_timestamp_ms": 1000, "brand_content_toggle": False, "brand_organic_toggle": True, "is_aigc": post.ai_made},
            "source_info": {"source": "FILE_UPLOAD", "video_size": size, "chunk_size": piece, "total_chunk_count": count},
        })).get("data") or {}
        publish_id, upload_url = str(started.get("publish_id") or ""), str(started.get("upload_url") or "")
        if not publish_id or not upload_url:
            raise net.NetworkError("TikTok did not accept the upload.")
        with post.video.path.open("rb") as file:
            for number in range(count):
                first = number * piece
                last = size - 1 if number == count - 1 else first + piece - 1
                chunk = file.read(last - first + 1)
                net.check(http.put(upload_url, content=chunk, headers={"Content-Type": post.video.mime_type, "Content-Length": str(len(chunk)),
                                                                       "Content-Range": f"bytes {first}-{last}/{size}"}), NAME)
        deadline = time.monotonic() + PREPARE_SECONDS
        while True:
            status = _body(http.post(f"{API}/post/publish/status/fetch/", headers=headers, json={"publish_id": publish_id})).get("data") or {}
            state = str(status.get("status") or "")
            if state == "PUBLISH_COMPLETE":
                ids = status.get("publicaly_available_post_id") or status.get("publicly_available_post_id") or []
                video_id = str(ids[0]) if ids else publish_id
                return Published(external_id=video_id, url=f"https://www.tiktok.com/video/{video_id}" if ids else "")
            if state == "FAILED":
                raise net.NetworkError(f"TikTok: {status.get('fail_reason') or 'the upload failed'}"[:400])
            if time.monotonic() > deadline:
                raise net.NetworkError("TikTok took too long to process the video; look in the TikTok app.")
            time.sleep(5)


def metrics(token: str, video_id: str) -> dict[str, int]:
    if not video_id.isdigit():
        return {}
    with net.client() as http:
        response = http.post(f"{API}/video/query/", params={"fields": "id,view_count,like_count,comment_count,share_count"},
                             headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"}, json={"filters": {"video_ids": [video_id]}})
    if response.status_code >= 400:
        return {}
    videos = (response.json().get("data") or {}).get("videos") or []
    if not videos:
        return {}
    v = videos[0]
    return {"views": int(v.get("view_count") or 0), "likes": int(v.get("like_count") or 0), "comments": int(v.get("comment_count") or 0), "shares": int(v.get("share_count") or 0)}


def followers(token: str) -> int | None:
    try:
        with net.client() as http:
            user = _user(http, token)
    except net.NetworkError:
        return None
    count = user.get("follower_count")
    return int(count) if count is not None else None
