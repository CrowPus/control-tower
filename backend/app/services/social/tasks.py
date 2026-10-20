"""The worker's part of social media: each approved post published at its
time, lost tasks picked up every minute, and what the posts achieved read every hour."""

from __future__ import annotations

from typing import Any

from ...db import transaction
from ...models import AgentTask, SocialPost
from ...workers.worker import task
from . import posts


@task("social.publish", queue="notifications")
def social_publish(job: AgentTask) -> dict[str, Any]:
    post_id = int(job.payload.get("post_id") or 0)
    # Claimed first, so a crash after this point never posts the same post twice.
    with transaction(write=True) as session:
        row = session.get(SocialPost, post_id, with_for_update=True)
        if row is None or row.status != "scheduled":
            return {"skipped": "not scheduled"}
        row.status = "publishing"
    with transaction(write=True) as session:
        row = session.get(SocialPost, post_id)
        if row is None:
            return {"skipped": "gone"}
        return posts.publish(session, row)


@task("social.due", queue="maintenance")
def social_due(_job: AgentTask) -> dict[str, Any]:
    from ..marketing import tasks as video_tasks

    with transaction(write=True) as session:
        return {"queued": posts.due(session), "videos": video_tasks.pick_up(session)}


@task("social.metrics", queue="maintenance")
def social_metrics(_job: AgentTask) -> dict[str, Any]:
    with transaction(write=True) as session:
        return posts.collect_metrics(session)
