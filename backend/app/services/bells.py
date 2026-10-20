"""The notification bell in the admin app: failed cleared proposals, open insight alerts and
incidents. Each admin has a "seen up to" moment; later events are new.
The text is not stored: the app words an event from its `kind` and `params`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..core.errors import AppError
from ..core.logging import get_logger, log_event
from ..models import AdminUser, TeamApproval
from . import system_mail

log = get_logger(__name__)

LIMIT = 30


@dataclass
class Item:
    id: str
    kind: str
    at: datetime
    unread: bool
    link: str
    params: dict[str, str] = field(default_factory=dict)


@dataclass
class Feed:
    unread: int
    items: list[Item]


# --- admin -----------------------------------------------------------------------------------------
def admin_feed(db: Session, admin: AdminUser) -> Feed:
    seen = admin.notifications_seen_at
    items: list[Item] = []
    # A proposal that was approved but failed (an agent's or Nia's): the owner hears it, never only the agent (2026-10-10).
    failed_since = datetime.now(UTC) - timedelta(days=7)
    for row in db.scalars(select(TeamApproval).where(TeamApproval.status == "failed", TeamApproval.executed_at >= failed_since)
                          .order_by(TeamApproval.executed_at.desc()).limit(20)):
        at = row.executed_at or row.created_at
        items.append(Item(id=f"p{row.id}", kind="proposal_failed", at=at, unread=seen is None or at > seen, link="/approvals",
                          params={"agent": row.agent.replace("_", " "), "title": row.title[:120], "id": str(row.id)}))
    # Open incidents from the insight alerts.
    from ..models import InsightAlert

    for alert in db.scalars(select(InsightAlert).where(InsightAlert.resolved_at.is_(None)).order_by(InsightAlert.opened_at.desc()).limit(10)):
        items.append(Item(id=f"a{alert.id}", kind="insight_critical" if alert.severity == "critical" else "insight_warning", at=alert.opened_at,
                          unread=seen is None or alert.opened_at > seen, link="/insights/alerts", params={"subject": alert.subject}))
    # Incidents the agents or the owner keep open.
    from ..models import Incident

    for inc in db.scalars(select(Incident).where(Incident.status != "resolved").order_by(Incident.started_at.desc()).limit(10)):
        items.append(Item(id=f"i{inc.id}", kind="incident_open", at=inc.started_at, unread=seen is None or inc.started_at > seen,
                          link="/product/incidents", params={"title": inc.title, "severity": inc.severity}))
    items.sort(key=lambda i: i.at, reverse=True)
    return Feed(unread=sum(1 for i in items if i.unread), items=items[:LIMIT])


def admin_seen(db: Session, admin: AdminUser) -> None:
    admin.notifications_seen_at = datetime.now(UTC)


# --- e-mail, for when nobody is looking at the bell -------------------------------------------------
def alert_admins(what: str, path: str) -> None:
    """One line to the owner's alert address (ADMIN_ALERT_EMAIL). Never blocks the event."""
    to = settings().admin_alert_email.strip()
    if not to:
        return
    base = (settings().admin_public_base_url or "http://localhost:8001").rstrip("/")
    try:
        system_mail.send("admin_attention", to=to, name="", locale="en", link=f"{base}{path}", extra={"what": what})
    except (AppError, OSError) as exc:
        log_event(log, "admin alert mail failed", error=type(exc).__name__)

