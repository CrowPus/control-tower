"""A LinkedIn company page through LinkedIn's Community Management API (an app in the owner's name that
LinkedIn approved for it). Tokens last 60 days; with a refresh token they are renewed on use."""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import quote, urlencode

from ...config import settings
from . import net
from .base import AccountInfo, Outgoing, Published

NAME = "LinkedIn"
SCOPES = ("w_organization_social", "r_organization_social", "rw_organization_admin")
API = "https://api.linkedin.com/rest"


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "LinkedIn-Version": settings().linkedin_api_version, "X-Restli-Protocol-Version": "2.0.0"}


def authorize_url(client_id: str, redirect_uri: str, state: str) -> str:
    query = {"response_type": "code", "client_id": client_id, "redirect_uri": redirect_uri, "state": state, "scope": " ".join(SCOPES)}
    return f"https://www.linkedin.com/oauth/v2/authorization?{urlencode(query)}"


def _tokens(body: dict[str, Any]) -> tuple[str, str, datetime | None]:
    expires = datetime.now(UTC) + timedelta(seconds=int(body.get("expires_in") or 0)) if body.get("expires_in") else None
    return str(body.get("access_token") or ""), str(body.get("refresh_token") or ""), expires


def connect(code: str, client_id: str, secret: str, redirect_uri: str) -> list[AccountInfo]:
    with net.client() as http:
        body = net.check(http.post("https://www.linkedin.com/oauth/v2/accessToken", data={"grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri,
                                                                                          "client_id": client_id, "client_secret": secret}), NAME)
        token, refresh, expires = _tokens(body)
        acls = net.check(http.get(f"{API}/organizationAcls", params={"q": "roleAssignee", "role": "ADMINISTRATOR", "state": "APPROVED"}, headers=_headers(token)), NAME)
        out: list[AccountInfo] = []
        for element in acls.get("elements") or []:
            urn = str(element.get("organization") or element.get("organizationTarget") or "")
            org_id = urn.rsplit(":", 1)[-1]
            if not org_id.isdigit():
                continue
            org = net.check(http.get(f"{API}/organizations/{org_id}", headers=_headers(token)), NAME)
            vanity = str(org.get("vanityName") or "")
            out.append(AccountInfo(network="linkedin", external_id=org_id, name=str(org.get("localizedName") or vanity or org_id), token=token, refresh=refresh,
                                   expires_at=expires, username=vanity, profile_url=f"https://www.linkedin.com/company/{vanity or org_id}/",
                                   followers=_followers(http, org_id, token)))
    if not out:
        raise net.NetworkError("LinkedIn gave no company page you administer. Sign in with a LinkedIn account that is an admin of the company's page.")
    return out


def refresh_token(refresh: str, client_id: str, secret: str) -> tuple[str, str, datetime | None]:
    with net.client() as http:
        body = net.check(http.post("https://www.linkedin.com/oauth/v2/accessToken", data={"grant_type": "refresh_token", "refresh_token": refresh,
                                                                                          "client_id": client_id, "client_secret": secret}), NAME)
    token, new_refresh, expires = _tokens(body)
    return token, new_refresh or refresh, expires


_RESERVED = re.compile(r"([\\|{}@\[\]()<>#*_~])")


def little_text(text: str) -> str:
    """LinkedIn's post text format: its reserved characters are escaped, so the text shows as written."""
    return _RESERVED.sub(r"\\\1", text)


def _upload_image(http: Any, org: str, token: str, data: bytes) -> str:
    started = net.check(http.post(f"{API}/images?action=initializeUpload", json={"initializeUploadRequest": {"owner": org}}, headers=_headers(token)), NAME)
    value = started.get("value") or {}
    response = http.put(value["uploadUrl"], content=data, headers={"Authorization": f"Bearer {token}"})
    if response.status_code >= 400:
        raise net.NetworkError(net.explain(response, NAME))
    return str(value["image"])


def _upload_video(http: Any, org: str, token: str, post: Outgoing) -> str:
    assert post.video is not None  # noqa: S101 - checked by the caller
    started = net.check(http.post(f"{API}/videos?action=initializeUpload", json={"initializeUploadRequest": {
        "owner": org, "fileSizeBytes": post.video.size, "uploadCaptions": False, "uploadThumbnail": False}}, headers=_headers(token)), NAME)
    value = started.get("value") or {}
    etags = []
    with post.video.path.open("rb") as file:
        for part in value.get("uploadInstructions") or []:
            first, last = int(part["firstByte"]), int(part["lastByte"])
            file.seek(first)
            response = http.put(part["uploadUrl"], content=file.read(last - first + 1), headers={"Content-Type": "application/octet-stream"})
            if response.status_code >= 400:
                raise net.NetworkError(net.explain(response, NAME))
            etags.append(response.headers.get("etag", "").strip('"'))
    net.check(http.post(f"{API}/videos?action=finalizeUpload", json={"finalizeUploadRequest": {"video": value["video"], "uploadToken": value.get("uploadToken", ""),
                                                                                              "uploadedPartIds": etags}}, headers=_headers(token)), NAME)
    return str(value["video"])


def publish(org_id: str, token: str, post: Outgoing) -> Published:
    org = f"urn:li:organization:{org_id}"
    text = post.text if not post.link or post.link in post.text else f"{post.text}\n\n{post.link}"
    body: dict[str, Any] = {"author": org, "commentary": little_text(text), "visibility": "PUBLIC", "lifecycleState": "PUBLISHED", "isReshareDisabledByAuthor": False,
                            "distribution": {"feedDistribution": "MAIN_FEED", "targetEntities": [], "thirdPartyDistributionChannels": []}}
    with net.client(timeout=600) as http:
        if post.kind == "video" and post.video:
            body["content"] = {"media": {"id": _upload_video(http, org, token, post), "title": post.title[:200]}}
        elif post.pictures:
            images = [{"id": _upload_image(http, org, token, p.data), "altText": p.alt_text[:300]} for p in post.pictures[:20]]
            body["content"] = {"media": images[0]} if len(images) == 1 else {"multiImage": {"images": images}}
        response = http.post(f"{API}/posts", json=body, headers=_headers(token))
        net.check(response, NAME)
    urn = response.headers.get("x-restli-id", "") or response.headers.get("x-linkedin-id", "")
    return Published(external_id=urn, url=f"https://www.linkedin.com/feed/update/{urn}/" if urn else "")


def _followers(http: Any, org_id: str, token: str) -> int | None:
    response = http.get(f"{API}/networkSizes/{quote(f'urn:li:organization:{org_id}')}", params={"edgeType": "COMPANY_FOLLOWED_BY_MEMBER"}, headers=_headers(token))
    if response.status_code >= 400:
        return None
    return int(response.json().get("firstDegreeSize") or 0)


def followers(org_id: str, token: str) -> int | None:
    with net.client() as http:
        return _followers(http, org_id, token)


def metrics(org_id: str, token: str, urn: str) -> dict[str, int]:
    kind = "ugcPosts" if ":ugcPost:" in urn else "shares"
    with net.client() as http:
        response = http.get(f"{API}/organizationalEntityShareStatistics?q=organizationalEntity&organizationalEntity={quote(f'urn:li:organization:{org_id}')}"
                            f"&{kind}=List({quote(urn)})", headers=_headers(token))
    if response.status_code >= 400:
        return {}
    elements = response.json().get("elements") or []
    stats = (elements[0].get("totalShareStatistics") if elements else None) or {}
    return {"views": int(stats.get("impressionCount") or 0), "reach": int(stats.get("uniqueImpressionsCount") or 0), "clicks": int(stats.get("clickCount") or 0),
            "likes": int(stats.get("likeCount") or 0), "comments": int(stats.get("commentCount") or 0), "shares": int(stats.get("shareCount") or 0)}
