"""The owner's developer apps at Meta, LinkedIn, Google and TikTok (Admin → Marketing → Connections): each
network lets a service post only through an app registered in the owner's name. The app's id is
shown; its secret is stored encrypted (core/crypto.py) and never shown again."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ...config import settings
from ...core import crypto
from .. import platform

Provider = Literal["meta", "linkedin", "google", "tiktok"]
PROVIDERS: tuple[Provider, ...] = ("meta", "linkedin", "google", "tiktok")
# Which networks each app connects.
NETWORKS_OF: dict[str, tuple[str, ...]] = {"meta": ("facebook", "instagram"), "linkedin": ("linkedin",), "google": ("youtube",), "tiktok": ("tiktok",)}
KEY = "social_apps"


class AppView(BaseModel):
    provider: str
    client_id: str
    secret_set: bool
    redirect_uri: str
    # Meta, Facebook Login for Business: the configuration (permissions chosen in the app) the sign-in uses.
    config_id: str = ""


class AppIn(BaseModel):
    client_id: str = Field(min_length=3, max_length=200)
    # None keeps the stored secret.
    client_secret: str | None = Field(default=None, max_length=500)
    config_id: str = Field(default="", max_length=40, pattern=r"^[0-9]*$")


def _all(session: Session) -> dict[str, Any]:
    value = platform.get(session, KEY) or {}
    return value if isinstance(value, dict) else {}


def redirect_uri(provider: str) -> str:
    base = (settings().admin_public_base_url or "").rstrip("/")
    return f"{base}/admin-api/marketing/connect/{provider}/callback" if base else ""


def view(session: Session, provider: str) -> AppView:
    raw = _all(session).get(provider) or {}
    return AppView(provider=provider, client_id=str(raw.get("client_id") or ""), secret_set=bool(raw.get("secret_enc")), redirect_uri=redirect_uri(provider),
                   config_id=str(raw.get("config_id") or ""))


def save(session: Session, provider: str, data: AppIn) -> AppView:
    stored = _all(session)
    current = dict(stored.get(provider) or {})
    entry = {"client_id": data.client_id.strip(), "secret_enc": current.get("secret_enc", ""), "config_id": data.config_id.strip()}
    if data.client_secret:
        entry["secret_enc"] = crypto.encrypt(data.client_secret.strip(), associated_data=f"social_app:{provider}")
    platform.put(session, KEY, {**stored, provider: entry})
    return view(session, provider)


def credentials(session: Session, provider: str) -> tuple[str, str]:
    raw = _all(session).get(provider) or {}
    token = str(raw.get("secret_enc") or "")
    secret = crypto.decrypt(token, associated_data=f"social_app:{provider}") if token else ""
    return str(raw.get("client_id") or ""), secret
