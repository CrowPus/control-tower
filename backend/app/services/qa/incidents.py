"""Incidents: what is broken, since when, whom it affects, the likely cause and the fix,
with a timeline of notes. The Engineer on Duty keeps them (an internal record, no approval: nothing leaves the install);
the owner reads them in Admin → Product → Incidents and may add a note or close one. A new critical or major
incident rings the admin bell and e-mails the team's alert address once."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...models import Incident

SEVERITIES = ("critical", "major", "minor")
STATUSES = ("open", "monitoring", "resolved")


def view(row: Incident) -> dict[str, Any]:
    return {"id": row.id, "title": row.title, "severity": row.severity, "status": row.status, "key": row.key, "impact": row.impact, "cause": row.cause,
            "fix": row.fix, "notes": row.notes, "opened_by": row.opened_by, "started_at": row.started_at.isoformat(),
            "resolved_at": row.resolved_at.isoformat() if row.resolved_at else None, "updated_at": row.updated_at.isoformat()}


def listing(session: Session, limit: int = 30) -> dict[str, Any]:
    open_rows = list(session.execute(select(Incident).where(Incident.status != "resolved").order_by(Incident.started_at.desc())).scalars())
    recent = list(session.execute(select(Incident).where(Incident.status == "resolved").order_by(Incident.resolved_at.desc()).limit(limit)).scalars())
    return {"open": [view(r) for r in open_rows], "recently_resolved": [view(r) for r in recent]}


def _note(row: Incident, by: str, text: str, now: datetime) -> None:
    if text.strip():
        row.notes = [*row.notes, {"at": now.isoformat(), "by": by, "text": text.strip()[:2000]}]


def open_or_note(session: Session, *, title: str, severity: str, key: str, impact: str = "", cause: str = "", fix: str = "", note: str = "", by: str) -> tuple[Incident, bool]:
    """A new incident, or a note on the open one about the same thing (key). True when it is new."""
    if severity not in SEVERITIES:
        raise ValueError(f"severity must be one of {', '.join(SEVERITIES)}")
    if not title.strip():
        raise ValueError("An incident needs a title: what is broken, in a few words.")
    now = datetime.now(UTC)
    key = key.strip()[:120]
    existing = session.execute(select(Incident).where(Incident.key == key, Incident.status != "resolved")).scalars().first() if key else None
    if existing is not None:
        _note(existing, by, note or impact or title, now)
        existing.updated_at = now
        session.flush()
        return existing, False
    row = Incident(title=title.strip()[:200], severity=severity, status="open", key=key, impact=impact.strip(), cause=cause.strip(), fix=fix.strip(),
                   notes=[], opened_by=by[:40], started_at=now, updated_at=now)
    _note(row, by, note, now)
    session.add(row)
    session.flush()
    if severity in ("critical", "major"):
        from ..bells import alert_admins

        alert_admins(("DRINGEND " if severity == "critical" else "") + f"Incident: {row.title}", "/product/incidents")
    return row, True


def update(session: Session, incident_id: int, *, by: str, note: str = "", status: str | None = None, severity: str | None = None, cause: str | None = None,
           fix: str | None = None, impact: str | None = None) -> Incident:
    row = session.get(Incident, incident_id)
    if row is None:
        raise ValueError("There is no such incident.")
    now = datetime.now(UTC)
    if status is not None:
        if status not in STATUSES:
            raise ValueError(f"status must be one of {', '.join(STATUSES)}")
        if status != row.status:
            _note(row, by, f"Status: {row.status} → {status}", now)
        row.status = status
        row.resolved_at = now if status == "resolved" else None
    if severity is not None:
        if severity not in SEVERITIES:
            raise ValueError(f"severity must be one of {', '.join(SEVERITIES)}")
        row.severity = severity
    for field, value in (("cause", cause), ("fix", fix), ("impact", impact)):
        if value is not None:
            setattr(row, field, value.strip())
    _note(row, by, note, now)
    row.updated_at = now
    session.flush()
    return row
