"""What the agent team really costs (2026-10-10: the Team page showed only the agents' text work — videos and pictures,
about half of the month, were left out). Everything the owner's own AI work records as "team": the agents' and Nia's
runs, chats, searches, browser and voice; the pictures from the studio; the videos and their checks.

Per agent: its own work (`agent.<key>.…`), the pictures it made (the media library knows who made each and what it
cost) and, for the Graphic Designer, its videos and their checks. Pictures you made yourself in the studio count to the
team, not to an agent. The budgets are unchanged: the team's AI budget limits the agents' own work; pictures and videos
have their own monthly budgets (Marketing).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ...models import AIUsage, MarketingMedia
from ..marketing import studio, video_studio

VIDEO_MAKER = "graphic_designer"  # videos are made only from the Graphic Designer's approved concepts
PICTURES = (studio.OPERATION,)
VIDEOS = (video_studio.OPERATION, video_studio.REVIEW_OPERATION)


def _sum(session: Session, since: datetime, *, prefix: str = "", operations: tuple[str, ...] = ()) -> float:
    stmt = select(func.coalesce(func.sum(AIUsage.estimated_cost_usd), 0.0)).where(AIUsage.created_at >= since)
    stmt = stmt.where(AIUsage.operation.like(f"{prefix}%")) if prefix else stmt.where(AIUsage.operation.in_(operations))
    return float(session.execute(stmt).scalar() or 0.0)


def team(session: Session, since: datetime) -> dict[str, float]:
    """The team's AI cost since a moment, with its parts: the agents' own work, pictures, videos."""
    agents = _sum(session, since, prefix="agent.")
    pictures = _sum(session, since, operations=PICTURES)
    videos = _sum(session, since, operations=VIDEOS)
    return {"total": round(agents + pictures + videos, 4), "agents": round(agents, 4), "pictures": round(pictures, 4), "videos": round(videos, 4)}


def agent(session: Session, key: str, since: datetime) -> float:
    """One agent's AI cost since a moment: its own work, the pictures it made, and its videos (Graphic Designer)."""
    own = _sum(session, since, prefix=f"agent.{key}.")
    pictures = float(session.execute(select(func.coalesce(func.sum(MarketingMedia.cost_usd), 0.0)).where(
        MarketingMedia.created_by == key, MarketingMedia.kind == "image", MarketingMedia.created_at >= since)).scalar() or 0.0)
    videos = _sum(session, since, operations=VIDEOS) if key == VIDEO_MAKER else 0.0
    return round(own + pictures + videos, 4)


def by_agent(session: Session, keys: list[str], since: datetime) -> dict[str, float]:
    return {k: agent(session, k, since) for k in keys}

