"""Connected social accounts: the owner presses Connect, signs in at the
network and allows the owner's app; the accounts it gives (Pages, Instagram, company page, channel) are
kept here with their tokens encrypted, renewed when they run out, and removed on Disconnect."""

from __future__ import annotations

import secrets
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...core import crypto
from ...core.errors import ValidationFailed
from ...models import SocialAccount
from .. import platform
from . import apps, linkedin, meta, net, tiktok, youtube
from .base import AccountInfo

STATE_KEY = "social_oauth_states"
STATE_MINUTES = 15

AUTHORIZE: dict[str, Callable[[str, str, str], str]] = {"meta": meta.authorize_url, "linkedin": linkedin.authorize_url, "google": youtube.authorize_url,
                                                     "tiktok": tiktok.authorize_url}
CONNECT: dict[str, Callable[[str, str, str, str], list[AccountInfo]]] = {"meta": meta.connect, "linkedin": linkedin.connect, "google": youtube.connect,
                                                                       "tiktok": tiktok.connect}
# The provider whose app renews an account's sign-in.
RENEWS: dict[str, str] = {"youtube": "google", "linkedin": "linkedin", "tiktok": "tiktok"}


def _aad(row: SocialAccount, kind: str) -> str:
    return f"social_account:{row.network}:{row.external_id}:{kind}"


def start(session: Session, provider: str, admin_id: int) -> str:
    """The network's sign-in address for this provider's app (with a one-time state)."""
    client_id, secret = apps.credentials(session, provider)
    if not client_id or not secret:
        raise ValidationFailed("Enter the app's ID and secret first (the steps are shown next to it).", code="SOCIAL_APP_MISSING")
    redirect = apps.redirect_uri(provider)
    if not redirect:
        raise ValidationFailed("The admin's public address (ADMIN_PUBLIC_BASE_URL) is not configured on the server.", code="SOCIAL_NO_REDIRECT")
    now = datetime.now(UTC)
    states = {k: v for k, v in (platform.get(session, STATE_KEY) or {}).items() if datetime.fromisoformat(v["expires"]) > now}
    state = secrets.token_urlsafe(24)
    states[state] = {"provider": provider, "admin_id": admin_id, "expires": (now + timedelta(minutes=STATE_MINUTES)).isoformat()}
    platform.put(session, STATE_KEY, states)
    if provider == "meta":
        return meta.authorize_url(client_id, redirect, state, apps.view(session, provider).config_id)
    return AUTHORIZE[provider](client_id, redirect, state)


def claim(session: Session, provider: str, state: str) -> int:
    """The admin who started this sign-in (the one-time state proves it; the network's redirect carries no
    admin cookie, which is SameSite=Strict). ValidationFailed when the state is unknown, used or expired."""
    states = dict(platform.get(session, STATE_KEY) or {})
    entry = states.pop(state, None) if state else None
    platform.put(session, STATE_KEY, states)
    if not entry or entry["provider"] != provider or datetime.fromisoformat(entry["expires"]) < datetime.now(UTC):
        raise ValidationFailed("This sign-in link expired or was not started here. Press Connect again.", code="SOCIAL_STATE")
    return int(entry["admin_id"])


def finish(session: Session, provider: str, code: str, admin_id: int) -> list[SocialAccount]:
    """The network sent the owner back: the accounts it gave are kept (or renewed)."""
    client_id, secret = apps.credentials(session, provider)
    try:
        infos = CONNECT[provider](code, client_id, secret, apps.redirect_uri(provider))
    except net.NetworkError as exc:
        raise ValidationFailed(str(exc), code="SOCIAL_CONNECT_FAILED") from exc
    now = datetime.now(UTC)
    kept: dict[str, SocialAccount] = {}
    for info in sorted(infos, key=lambda i: i.network == "instagram"):  # Pages first: Instagram points at its Page
        row = session.execute(select(SocialAccount).where(SocialAccount.network == info.network, SocialAccount.external_id == info.external_id)).scalar_one_or_none()
        if row is None:
            row = SocialAccount(network=info.network, external_id=info.external_id, created_at=now)
            session.add(row)
        row.name, row.username, row.picture_url, row.profile_url = info.name[:200], info.username[:200], info.picture_url[:1000], info.profile_url[:500]
        row.status, row.last_error, row.updated_at, row.connected_by_admin_id = "ok", "", now, admin_id
        row.token_enc = crypto.encrypt(info.token, associated_data=_aad(row, "token")) if info.token else ""
        row.refresh_enc = crypto.encrypt(info.refresh, associated_data=_aad(row, "refresh")) if info.refresh else ""
        row.token_expires_at = info.expires_at
        if info.followers is not None:
            row.followers, row.followers_at = int(info.followers), now
        if info.parent_external_id:
            parent = kept.get(f"facebook:{info.parent_external_id}")
            row.parent_id = parent.id if parent else None
        session.flush()
        kept[f"{row.network}:{row.external_id}"] = row
    return list(kept.values())


def token(session: Session, row: SocialAccount) -> str:
    """The account's token to publish with, renewed first when it has run out (LinkedIn, YouTube, TikTok)."""
    if row.network == "instagram":
        parent = session.get(SocialAccount, row.parent_id) if row.parent_id else None
        if parent is None:
            raise net.NetworkError("The Instagram account's Facebook Page is not connected any more; connect Facebook and Instagram again.")
        return token(session, parent)
    if row.token_expires_at and row.token_expires_at < datetime.now(UTC) + timedelta(minutes=5):
        provider = RENEWS.get(row.network, row.network)
        refresh = crypto.decrypt(row.refresh_enc, associated_data=_aad(row, "refresh")) if row.refresh_enc else ""
        if not refresh:
            row.status, row.last_error = "error", "The sign-in ran out; press Connect again."
            raise net.NetworkError(f"The {row.network} sign-in ran out; the owner needs to press Connect again.")
        client_id, secret = apps.credentials(session, provider)
        renew = {"google": youtube.refresh_token, "linkedin": linkedin.refresh_token, "tiktok": tiktok.refresh_token}[provider]
        new_token, new_refresh, expires = renew(refresh, client_id, secret)
        row.token_enc = crypto.encrypt(new_token, associated_data=_aad(row, "token"))
        row.refresh_enc = crypto.encrypt(new_refresh, associated_data=_aad(row, "refresh")) if new_refresh else row.refresh_enc
        row.token_expires_at = expires
        return new_token
    if not row.token_enc:
        raise net.NetworkError(f"{row.name} has no sign-in; press Connect again.")
    return crypto.decrypt(row.token_enc, associated_data=_aad(row, "token"))


def connected(session: Session, network: str) -> SocialAccount | None:
    """The account a post to this network goes to: the first connected one that works."""
    return session.execute(select(SocialAccount).where(SocialAccount.network == network, SocialAccount.status != "off").order_by(SocialAccount.id)).scalars().first()


def view(row: SocialAccount) -> dict[str, Any]:
    return {"id": row.id, "network": row.network, "external_id": row.external_id, "name": row.name, "username": row.username, "picture_url": row.picture_url, "profile_url": row.profile_url,
            "status": row.status, "last_error": row.last_error, "followers": row.followers, "followers_at": row.followers_at,
            "token_expires_at": row.token_expires_at, "connected_at": row.created_at}
