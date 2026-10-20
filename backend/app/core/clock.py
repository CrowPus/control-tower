"""The owner's time: schedules, posting hours and the days Insights counts are in the owner's time zone (TIME_ZONE)."""

from __future__ import annotations

from datetime import date, datetime
from functools import lru_cache
from zoneinfo import ZoneInfo

from ..config import settings


@lru_cache
def zone() -> ZoneInfo:
    return ZoneInfo(settings().time_zone or "UTC")


def local_day(moment: datetime | None = None) -> date:
    """The owner's calendar day of a moment (now when none is given)."""
    return (moment or datetime.now(zone())).astimezone(zone()).date()
