"""The owner's trust rules (Admin → Trust → Rules; platform setting `trust`). Empty means not set: nothing is
measured against a number the owner has not given."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from .. import platform

KEY = "trust"


@dataclass(frozen=True)
class Rules:
    # A proposal waiting for clearance longer than this is called out to the owner.
    wait_alert_days: int | None = None


FIELDS = ("wait_alert_days",)


def get(session: Session) -> Rules:
    raw = platform.get(session, KEY) or {}
    return Rules(**{k: int(v) for k, v in raw.items() if k in FIELDS and isinstance(v, int) and not isinstance(v, bool) and v > 0})


def put(session: Session, values: dict[str, int | None]) -> Rules:
    platform.put(session, KEY, {k: v for k, v in values.items() if k in FIELDS and v is not None})
    return get(session)
