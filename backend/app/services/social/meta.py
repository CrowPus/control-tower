"""Facebook Pages and Instagram business accounts through Meta's Graph API (one app in the owner's name;
in development mode it posts to the owner's own Pages without Meta's review).

Connecting gives a long-lived Page token for each Page the owner manages (it does not expire while the
owner stays admin of the Page and keeps the app's permissions) and the Instagram business account
linked to each Page, which is published with its Page's token.
"""

from __future__ import annotations

import time
from typing import Any
from urllib.parse import urlencode

from ...config import settings
from . import net
from .base import AccountInfo, Outgoing, Published

NAME = "Meta"
SCOPES = ("pages_show_list", "pages_read_engagement", "pages_manage_posts", "pages_read_user_content", "read_insights", "business_management",
          "instagram_basic", "instagram_content_publish", "instagram_manage_insights", "instagram_manage_comments")
# How long Instagram may take to prepare a video before it is given up.
PREPARE_SECONDS = 600


def graph() -> str:
    return f"https://graph.facebook.com/{settings().meta_graph_version}"


def authorize_url(client_id: str, redirect_uri: str, state: str, config_id: str = "") -> str:
    """Facebook Login for Business takes a configuration (made in the app, with its permissions) instead of a scope list."""
    query = {"client_id": client_id, "redirect_uri": redirect_uri, "state": state, "response_type": "code"}
    if config_id:
        query.update({"config_id": config_id, "override_default_response_type": "true"})
    else:
        query["scope"] = ",".join(SCOPES)
    return f"https://www.facebook.com/{settings().meta_graph_version}/dialog/oauth?{urlencode(query)}"


def connect(code: str, client_id: str, secret: str, redirect_uri: str) -> list[AccountInfo]:
    with net.client() as http:
        short = net.check(http.get(f"{graph()}/oauth/access_token", params={"client_id": client_id, "client_secret": secret, "redirect_uri": redirect_uri, "code": code}), NAME)
        long = net.check(http.get(f"{graph()}/oauth/access_token", params={"grant_type": "fb_exchange_token", "client_id": client_id, "client_secret": secret,
                                                                            "fb_exchange_token": short["access_token"]}), NAME)
        fields = "id,name,access_token,link,followers_count,fan_count,picture{url},instagram_business_account{id,username,name,profile_picture_url,followers_count}"
        pages = net.check(http.get(f"{graph()}/me/accounts", params={"fields": fields, "limit": 50, "access_token": long["access_token"]}), NAME)
    out: list[AccountInfo] = []
    for page in pages.get("data") or []:
        token = str(page.get("access_token") or "")
        if not token:
            continue
        out.append(AccountInfo(network="facebook", external_id=str(page["id"]), name=str(page.get("name") or ""), token=token,
                               picture_url=str(((page.get("picture") or {}).get("data") or {}).get("url") or ""), profile_url=str(page.get("link") or ""),
                               followers=page.get("followers_count") or page.get("fan_count")))
        ig = page.get("instagram_business_account") or {}
        if ig.get("id"):
            out.append(AccountInfo(network="instagram", external_id=str(ig["id"]), name=str(ig.get("name") or ig.get("username") or ""), token="",
                                   username=str(ig.get("username") or ""), picture_url=str(ig.get("profile_picture_url") or ""),
                                   profile_url=f"https://www.instagram.com/{ig.get('username')}/" if ig.get("username") else "", followers=ig.get("followers_count"),
                                   parent_external_id=str(page["id"])))
    if not out:
        raise net.NetworkError("Meta gave no Facebook Page. Choose your Page (and its Instagram account) when Facebook asks which ones the app may use.")
    return out


# --- publishing ------------------------------------------------------------------------------------------------
def publish_facebook(page_id: str, token: str, post: Outgoing) -> Published:
    message = post.text if not post.link or post.link in post.text else f"{post.text}\n\n{post.link}"
    with net.client(timeout=300) as http:
        if post.kind == "video" and post.video:
            body = net.check(http.post(f"{graph()}/{page_id}/videos", data={"file_url": post.video.url, "description": message, "title": post.title, "access_token": token}), NAME)
            video_id = str(body.get("id") or "")
            return Published(external_id=video_id, url=f"https://www.facebook.com/{page_id}/videos/{video_id}")
        if post.kind == "image" and post.pictures:
            body = net.check(http.post(f"{graph()}/{page_id}/photos", data={"url": post.pictures[0].url, "message": message, "access_token": token}), NAME)
            post_id = str(body.get("post_id") or body.get("id") or "")
            return Published(external_id=post_id, url=f"https://www.facebook.com/{post_id}")
        if post.kind == "carousel" and post.pictures:
            ids = []
            for picture in post.pictures[:10]:
                photo = net.check(http.post(f"{graph()}/{page_id}/photos", data={"url": picture.url, "published": "false", "access_token": token}), NAME)
                ids.append(str(photo["id"]))
            data: dict[str, Any] = {"message": message, "access_token": token}
            for i, media_id in enumerate(ids):
                data[f"attached_media[{i}]"] = f'{{"media_fbid":"{media_id}"}}'
            body = net.check(http.post(f"{graph()}/{page_id}/feed", data=data), NAME)
            return Published(external_id=str(body.get("id") or ""), url=f"https://www.facebook.com/{body.get('id')}")
        data = {"message": post.text, "access_token": token}
        if post.link:
            data["link"] = post.link
        body = net.check(http.post(f"{graph()}/{page_id}/feed", data=data), NAME)
        return Published(external_id=str(body.get("id") or ""), url=f"https://www.facebook.com/{body.get('id')}")


def _wait(http: Any, container: str, token: str) -> None:
    deadline = time.monotonic() + PREPARE_SECONDS
    while True:
        state = net.check(http.get(f"{graph()}/{container}", params={"fields": "status_code,status", "access_token": token}), NAME)
        code = str(state.get("status_code") or "")
        if code in ("FINISHED", "PUBLISHED"):
            return
        if code in ("ERROR", "EXPIRED"):
            raise net.NetworkError(f"Instagram could not prepare the media: {state.get('status') or code}")
        if time.monotonic() > deadline:
            raise net.NetworkError("Instagram took too long to prepare the video; try again later.")
        time.sleep(sleep_seconds)


# Tests set this to 0.
sleep_seconds = 5.0


def publish_instagram(ig_id: str, token: str, post: Outgoing) -> Published:
    caption = f"{post.text}\n\n{post.link}" if post.link and post.link not in post.text else post.text
    with net.client(timeout=300) as http:
        if post.kind == "video" and post.video:
            container = net.check(http.post(f"{graph()}/{ig_id}/media", data={"media_type": "REELS", "video_url": post.video.url, "caption": caption,
                                                                                "share_to_feed": "true", "access_token": token}), NAME)["id"]
        elif post.kind == "carousel" and len(post.pictures) > 1:
            children = []
            for picture in post.pictures[:10]:
                child = net.check(http.post(f"{graph()}/{ig_id}/media", data={"image_url": picture.url, "is_carousel_item": "true", "access_token": token}), NAME)["id"]
                children.append(str(child))
            for child in children:
                _wait(http, child, token)
            container = net.check(http.post(f"{graph()}/{ig_id}/media", data={"media_type": "CAROUSEL", "children": ",".join(children), "caption": caption,
                                                                                "access_token": token}), NAME)["id"]
        elif post.pictures:
            data = {"image_url": post.pictures[0].url, "caption": caption, "access_token": token}
            if post.pictures[0].alt_text:
                data["alt_text"] = post.pictures[0].alt_text[:1000]
            container = net.check(http.post(f"{graph()}/{ig_id}/media", data=data), NAME)["id"]
        else:
            raise net.NetworkError("Instagram needs a picture or a video.")
        _wait(http, str(container), token)
        media = net.check(http.post(f"{graph()}/{ig_id}/media_publish", data={"creation_id": container, "access_token": token}), NAME)
        media_id = str(media.get("id") or "")
        link = net.check(http.get(f"{graph()}/{media_id}", params={"fields": "permalink", "access_token": token}), NAME)
        return Published(external_id=media_id, url=str(link.get("permalink") or f"https://www.instagram.com/{ig_id}/"))


# --- numbers -------------------------------------------------------------------------------------------------------
def _safe(http: Any, url: str, params: dict[str, Any]) -> dict[str, Any]:
    response = http.get(url, params=params)
    if response.status_code >= 400:
        return {}
    body = response.json()
    return body if isinstance(body, dict) else {}


def metrics_facebook(post_id: str, token: str) -> dict[str, int]:
    out: dict[str, int] = {}
    with net.client() as http:
        body = _safe(http, f"{graph()}/{post_id}", {"fields": "reactions.summary(total_count),comments.summary(total_count),shares", "access_token": token})
        out["likes"] = int(((body.get("reactions") or {}).get("summary") or {}).get("total_count") or 0)
        out["comments"] = int(((body.get("comments") or {}).get("summary") or {}).get("total_count") or 0)
        out["shares"] = int((body.get("shares") or {}).get("count") or 0)
        for metric, key in (("post_impressions_unique", "reach"), ("post_clicks", "clicks"), ("post_video_views", "views")):
            data = _safe(http, f"{graph()}/{post_id}/insights", {"metric": metric, "access_token": token}).get("data") or []
            if data:
                values = data[0].get("values") or [{}]
                out[key] = int(values[-1].get("value") or 0)
    return out


def metrics_instagram(media_id: str, token: str) -> dict[str, int]:
    out: dict[str, int] = {}
    with net.client() as http:
        base = _safe(http, f"{graph()}/{media_id}", {"fields": "like_count,comments_count", "access_token": token})
        out["likes"], out["comments"] = int(base.get("like_count") or 0), int(base.get("comments_count") or 0)
        for metric in ("reach", "views", "saved", "shares"):
            data = _safe(http, f"{graph()}/{media_id}/insights", {"metric": metric, "access_token": token}).get("data") or []
            if data:
                values = data[0].get("values") or [{}]
                total = data[0].get("total_value") or {}
                out["saves" if metric == "saved" else metric] = int(total.get("value") or values[-1].get("value") or 0)
    return out


def followers(network: str, external_id: str, token: str) -> int | None:
    with net.client() as http:
        fields = "followers_count,fan_count" if network == "facebook" else "followers_count"
        body = _safe(http, f"{graph()}/{external_id}", {"fields": fields, "access_token": token})
    value = body.get("followers_count") or body.get("fan_count")
    return int(value) if value is not None else None

