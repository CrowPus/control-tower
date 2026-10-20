"""The worker's part of the video studio: each video being made is looked at every
few seconds until all its scenes are made and joined; one look at a time per video."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...db import transaction
from ...models import AgentTask, MarketingVideo
from ...workers.worker import task
from . import video_studio

# Safety rail: a video still being made after this long is stopped (its scenes' operations are long gone).
GIVE_UP = timedelta(hours=3)


@task("marketing.video_step", queue="default")
def video_step(job: AgentTask) -> dict[str, Any]:
    video_id = int(job.payload.get("video_id") or 0)
    with transaction(write=True) as session:
        row = session.get(MarketingVideo, video_id, with_for_update=True)
        if row is None or row.status != "making":
            return {"skipped": "not being made"}
        if video_studio.step(session, row):
            video_studio.queue_step(session, row, next_look=True)
        return {"video": row.id, "status": row.status, "scenes": [s.get("state") for s in (row.progress or {}).get("scenes") or []]}


@task("marketing.video_review", queue="default")
def video_review(job: AgentTask) -> dict[str, Any]:
    return video_studio.review_now(int(job.payload.get("video_id") or 0), lambda: transaction(write=True))


def pick_up(session: Session) -> int:
    """Videos being made whose next look was lost (a restart): looked at again; those far too long underway are stopped."""
    now = datetime.now(UTC)
    count = 0
    for row in session.execute(select(MarketingVideo).where(MarketingVideo.status == "making")).scalars().all():
        progress = row.progress or {}
        moments = [datetime.fromisoformat(str(s["started_at"])) for s in progress.get("scenes") or [] if s.get("started_at")]
        if progress.get("restarted_at"):
            moments.append(datetime.fromisoformat(str(progress["restarted_at"])))
        started = max([*moments, row.created_at])
        if now - started > GIVE_UP:
            row.status, row.error = "failed", "Making this video stopped; try it again."
            continue
        video_studio.queue_step(session, row)
        count += 1
    return count
