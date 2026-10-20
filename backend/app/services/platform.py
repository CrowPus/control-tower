"""Install-wide settings the admin changes (the `platform_settings` table).

Every business number lives here, set by the owner in the admin; the code reads it.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import PlatformSetting

SWITCHES = ("paused_all",)


def defaults() -> dict[str, Any]:
    return {
        "maintenance_banner": "",
        # Insights: alert thresholds, and the week the Monday report last went out.
        "insights": {},
        # The agents (services/team/config.py): every agent off and without budget, schedule or tools until the owner
        # sets them.
        "agents": {},
        # Nia's browser (services/nia/browser.py): switched on, minutes and steps per session, blocked sites, sites
        # that stay signed in. Empty = off.
        "nia_browser": {},
        # Who Nia talks with (services/nia/memory.py): the owner's name, how Nia addresses them, their role, what they told Nia.
        "nia_owner": {},
        # Trust (services/trust): the owner's rules for reviewing the agents' work. Empty = not set.
        "trust": {},
        # The QA checks (services/qa/checks.py): the addresses to check and the owner's rules.
        "qa": {},
        # The social networks the owner posts on (services/social): agents write posts only for these.
        "social": {},
        # Talking to Nia (services/nia/voice.py): the voice and the most minutes a day; empty = no voice.
        "nia": {},
        # Mailboxes the agents use (services/team/mailboxes.py); passwords stored encrypted.
        "mailboxes": {},
        # The studio: the brand kit, the owner's marketing settings (picture budget, posting hours), the developer apps
        # at Meta/LinkedIn/Google (secrets encrypted), and sign-ins under way.
        "brand": {},
        "marketing": {},
        "social_apps": {},
        "social_oauth_states": {},
        # The company's character in its videos: the owner's description and pictures.
        "character": {},
        # Outside agents (services/outside): the pause for all, credential days, daily calls. Empty: the defaults.
        "outside": {},
        # Plugins (services/plugins): which are switched on, their settings (secrets encrypted) and shared secret, the
        # last collection of their numbers; and the projects connected over HTTP with their manifests.
        "plugins": {},
        "plugin_connections": {},
        # The AI the install thinks with (ai/providers.py): provider, keys (encrypted), models, prices.
        "ai": {},
        # The first-run setup (admin/setup_routes.py): done or not.
        "setup": {},
        # Kill switches: stop all automation at once.
        "switches": {name: False for name in SWITCHES},
    }


def get(session: Session, key: str) -> Any:
    row = session.execute(select(PlatformSetting).where(PlatformSetting.key == key)).scalar_one_or_none()
    return row.value if row is not None else defaults()[key]


def get_all(session: Session) -> dict[str, Any]:
    stored = {row.key: row.value for row in session.execute(select(PlatformSetting)).scalars()}
    return {**defaults(), **stored}


def put(session: Session, key: str, value: Any) -> None:
    if key not in defaults():
        raise KeyError(key)
    now = datetime.now(UTC)
    row = session.execute(select(PlatformSetting).where(PlatformSetting.key == key)).scalar_one_or_none()
    if row is None:
        session.add(PlatformSetting(key=key, value=value, created_at=now, updated_at=now))
    else:
        row.value = value
        row.updated_at = now


def switches(session: Session) -> dict[str, bool]:
    stored = get(session, "switches") or {}
    return {name: bool(stored.get(name, False)) for name in SWITCHES}
