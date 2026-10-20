"""Social posts. An agent proposes a post (social.post) for one or more
networks: its text (per network where it differs), pictures from the media library or a video script,
the link and the time. When the owner approves it, one post per network is kept:

* on a connected network it is scheduled and the service publishes it at its time;
* a video post waits for the owner's video ("needs video") until the owner links the file;
* on a network that is not connected (or X) it waits to be posted by hand, as before.

Afterwards the service reads what each post achieved (reach, views, likes, comments, shares, clicks).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from ...core import clock
from ...models import (
    NETWORKS,
    MarketingMedia,
    MarketingPlan,
    SocialAccount,
    SocialPost,
    TeamApproval,
)
from ...workers import queue as q
from .. import platform
from ..marketing import brand, studio, video_studio
from ..team import approvals
from ..team.approvals import executor
from . import accounts, linkedin, meta, net, tiktok, video, youtube
from .base import Outgoing, Picture, Published, Video

LIMITS = {"x": 280, "linkedin": 3000, "facebook": 5000, "instagram": 2200, "youtube": 5000, "tiktok": 2200}
KINDS = ("text", "image", "carousel", "video")
# What each network can carry.
CAN = {"facebook": set(KINDS), "instagram": {"image", "carousel", "video"}, "linkedin": set(KINDS), "youtube": {"video"}, "tiktok": {"video"}, "x": {"text", "image"}}
METRICS_EVERY = timedelta(hours=3)
METRICS_FOR = timedelta(days=30)


def networks(session: Session) -> list[str]:
    """The networks posts may go to: the connected ones and those the owner posts on by hand."""
    connected = {a.network for a in session.execute(select(SocialAccount).where(SocialAccount.status != "off")).scalars()}
    chosen = set((platform.get(session, "social") or {}).get("networks") or [])
    return [n for n in NETWORKS if n in connected or n in chosen]


def overview(session: Session) -> dict[str, Any]:
    recent = session.execute(select(SocialPost).order_by(SocialPost.id.desc()).limit(30)).scalars()
    connected = [accounts.view(a) for a in session.execute(select(SocialAccount).where(SocialAccount.status != "off")).scalars()]
    return {"networks": networks(session), "connected": [{"network": a["network"], "name": a["name"], "followers": a["followers"]} for a in connected],
            "posts": [{"id": p.id, "network": p.network, "kind": p.kind, "status": p.status, "text": p.text[:300], "link": p.link,
                       "scheduled_at": p.scheduled_at.isoformat() if p.scheduled_at else None, "posted_at": p.posted_at.isoformat() if p.posted_at else None,
                       "metrics": p.metrics} for p in recent],
            "note": None if networks(session) else "No network is connected or chosen yet; no post can be made."}


def _next_slot(session: Session, now: datetime) -> datetime:
    """The owner's next posting hour (the owner's time zone), or now when none is set."""
    hours = sorted({h for h in brand.settings_of(session).posting_hours if 0 <= h <= 23})
    if not hours:
        return now
    local = now.astimezone(clock.zone())
    for days in range(0, 8):
        day = (local + timedelta(days=days)).date()
        for hour in hours:
            slot = datetime(day.year, day.month, day.day, hour, tzinfo=clock.zone())
            if slot > local + timedelta(minutes=2):
                return slot.astimezone(UTC)
    return now


def _text_for(payload: dict[str, Any], network: str) -> str:
    raw = payload.get("texts")
    texts: dict[str, Any] = raw if isinstance(raw, dict) else {}
    return str(texts.get(network) or payload.get("text") or "").strip()


def check(session: Session, payload: dict[str, Any]) -> list[str]:
    """What is wrong with a proposed post (empty: it can go out)."""
    problems: list[str] = []
    wanted = payload.get("networks") or ([payload["network"]] if payload.get("network") else [])
    if not isinstance(wanted, list) or not wanted:
        return ["Name the networks (networks: [\"instagram\", \"facebook\", …])."]
    allowed = networks(session)
    kind = str(payload.get("kind") or ("image" if payload.get("media_ids") else "text"))
    if kind not in KINDS:
        problems.append(f"kind is one of {', '.join(KINDS)}.")
    media_ids = [int(m) for m in payload.get("media_ids") or [] if str(m).isdigit()]
    media = {m.id: m for m in session.execute(select(MarketingMedia).where(MarketingMedia.id.in_(media_ids))).scalars()} if media_ids else {}
    if len(media) != len(set(media_ids)):
        problems.append("A picture in media_ids is not in the media library.")
    if any(m.source == "storyboard" for m in media.values()):
        problems.append("A storyboard frame is part of a video concept, not a picture to post (it has no logo or AI label).")
    if kind in ("image", "carousel") and not [m for m in media.values() if m.kind == "image"]:
        problems.append("A picture post needs pictures from the media library (create_image or the owner's uploads).")
    if kind == "carousel" and not 2 <= len(media_ids) <= 10:
        problems.append("A carousel has 2 to 10 pictures.")
    if kind == "video" and not ([m for m in media.values() if m.kind == "video"] or payload.get("video_script")):
        problems.append("A video post needs the owner's video (media_ids) or a video_script for the owner to film.")
    for network in wanted:
        if network not in NETWORKS:
            problems.append(f"{network} is not a network the company posts on ({', '.join(NETWORKS)}).")
            continue
        if network not in allowed:
            problems.append(f"{network} is not connected or chosen by the owner (Admin → Marketing → Connections).")
        if kind in KINDS and kind not in CAN[network]:
            problems.append(f"{network} does not take {kind} posts.")
        text = _text_for(payload, network)
        if not text and network != "youtube":
            problems.append(f"The text for {network} is empty.")
        if len(text) > LIMITS[network]:
            problems.append(f"Too long for {network}: {len(text)} of {LIMITS[network]} characters.")
    link = str(payload.get("link") or "").strip()
    if link and not link.startswith("https://"):
        problems.append("A link starts with https://.")
    when = payload.get("publish_at")
    if when:
        try:
            moment = datetime.fromisoformat(str(when).replace("Z", "+00:00"))
            if moment.tzinfo is None:
                moment = moment.replace(tzinfo=clock.zone())
            if moment < datetime.now(UTC) - timedelta(minutes=5):
                problems.append("publish_at is in the past.")
        except ValueError:
            problems.append("publish_at is a date and time like 2026-10-08T09:00 (the owner's time zone).")
    return problems


approvals.CHECKS["social.post"] = lambda session, payload: check(session, payload)


@executor("social.post")
def _post(session: Session, approval: TeamApproval, payload: dict[str, Any]) -> dict[str, Any]:
    problems = check(session, payload)
    if problems:
        raise ValueError(" ".join(problems))
    now = datetime.now(UTC)
    kind = str(payload.get("kind") or ("image" if payload.get("media_ids") else "text"))
    media_ids = [int(m) for m in payload.get("media_ids") or []]
    when_raw = payload.get("publish_at")
    when = None
    if when_raw:
        when = datetime.fromisoformat(str(when_raw).replace("Z", "+00:00"))
        when = (when if when.tzinfo else when.replace(tzinfo=clock.zone())).astimezone(UTC)
    has_video = bool(media_ids) and any(m.kind == "video" for m in session.execute(select(MarketingMedia).where(MarketingMedia.id.in_(media_ids))).scalars())
    plan = session.execute(select(MarketingPlan.id).where(MarketingPlan.status == "active").order_by(MarketingPlan.id.desc())).scalar()
    created = []
    wanted = payload.get("networks") or [payload.get("network")]
    for network in wanted:
        account = accounts.connected(session, network) if network != "x" else None
        if kind == "video" and not has_video:
            status = "needs_video"
        elif account is None:
            status = "ready"  # posted by hand
        else:
            status = "scheduled"
        row = SocialPost(network=network, text=_text_for(payload, network), link=str(payload.get("link") or "")[:500], image_note=str(payload.get("image_note") or "")[:2000],
                         agent=approval.agent, approval_id=approval.id, status=status, kind=kind, title=str(payload.get("title") or "")[:200], media_ids=media_ids,
                         video_script=payload.get("video_script") if isinstance(payload.get("video_script"), dict) else {}, account_id=account.id if account else None,
                         scheduled_at=(when or _next_slot(session, now)) if status in ("scheduled", "needs_video") else None, plan_id=plan, created_at=now)
        session.add(row)
        session.flush()
        if status == "scheduled":
            schedule(session, row)
        created.append({"post": row.id, "network": network, "status": status})
    return {"posts": created}


def schedule(session: Session, row: SocialPost) -> None:
    """Have the post published at its time. A job already waiting for this post is moved to the new time (Publish now, a
    changed time): only one job per post, and never one that keeps an old date."""
    from ...models import AgentTask

    when = row.scheduled_at or datetime.now(UTC)
    key = f"social.publish:{row.id}"
    # Named columns: the admin connection may move a waiting task but not read its payload.
    waiting = session.execute(select(AgentTask.id).where(AgentTask.dedupe_key == key, AgentTask.status == "queued")).scalar_one_or_none()
    if waiting is not None:
        session.execute(update(AgentTask).where(AgentTask.id == waiting).values(run_at=when, updated_at=datetime.now(UTC)))
        return
    q.enqueue(session, "social.publish", {"post_id": row.id}, queue="notifications", priority=2, run_at=when, max_attempts=1, dedupe_key=key)


def _outgoing(session: Session, row: SocialPost, fetched: video.Fetched | None, *, url: str = "") -> Outgoing:
    media = {m.id: m for m in session.execute(select(MarketingMedia).where(MarketingMedia.id.in_(row.media_ids or []))).scalars()}
    ordered = [media[i] for i in row.media_ids or [] if i in media]
    pictures = [Picture(data=m.data or b"", mime_type=m.mime_type, url=studio.public_url(m), alt_text=m.alt_text) for m in ordered if m.kind == "image" and m.data]
    ai_made = any(m.ai_generated for m in ordered)
    text = row.text
    label = brand.kit(session).ai_label
    if any(m.ai_generated and m.shows_people for m in ordered) and label and label.lower() not in text.lower():
        # People made by AI are said to be so, in the post as well as on the picture.
        text = f"{text}\n\n({label})"
    clip = Video(path=fetched.path, size=fetched.size, url=url or fetched.public_url(), mime_type=fetched.mime_type) if fetched else None
    return Outgoing(kind=row.kind, text=text, link=row.link, title=row.title, pictures=pictures, video=clip, ai_made=ai_made)


def _send(session: Session, row: SocialPost, account: SocialAccount, post: Outgoing) -> Published:
    token = accounts.token(session, account)
    if account.network == "facebook":
        return meta.publish_facebook(account.external_id, token, post)
    if account.network == "instagram":
        return meta.publish_instagram(account.external_id, token, post)
    if account.network == "linkedin":
        return linkedin.publish(account.external_id, token, post)
    if account.network == "youtube":
        return youtube.publish(token, post)
    if account.network == "tiktok":
        return tiktok.publish(token, post)
    raise net.NetworkError(f"{account.network} is posted by hand.")


def publish(session: Session, row: SocialPost) -> dict[str, Any]:
    """Publish one scheduled post now (the worker, at its time)."""
    account = session.get(SocialAccount, row.account_id) if row.account_id else accounts.connected(session, row.network)
    if account is None:
        row.status, row.error = "ready", "The network is not connected any more; post it by hand or connect it again."
        return {"skipped": "not connected"}
    row.account_id = account.id
    clip_row = next((m for m in session.execute(select(MarketingMedia).where(MarketingMedia.id.in_(row.media_ids or []))).scalars() if m.kind == "video"), None)
    try:
        made = video_studio.file_of(clip_row) if clip_row is not None and clip_row.file else None
        if row.kind == "video" and clip_row is not None and clip_row.file and made is None:
            raise net.NetworkError("The video's file was deleted; add another video.")
        if row.kind == "video" and made is not None and clip_row is not None:
            # A video the studio made: sent from its file, its public address for the networks that fetch it.
            fetched = video.Fetched(path=made, token=clip_row.public_token, size=made.stat().st_size, mime_type="video/mp4")
            published = _send(session, row, account, _outgoing(session, row, fetched, url=studio.public_url(clip_row)))
        elif row.kind == "video" and clip_row is not None and clip_row.link:
            with video.fetched(clip_row.link) as clip:
                published = _send(session, row, account, _outgoing(session, row, clip))
        else:
            published = _send(session, row, account, _outgoing(session, row, None))
    except net.NetworkError as exc:
        row.status, row.error = "failed", str(exc)[:500]
        return {"failed": row.error}
    except Exception as exc:  # noqa: BLE001 - a network's surprise is shown on the post, never lost
        row.status, row.error = "failed", f"{account.network} failed ({exc.__class__.__name__})."[:500]
        return {"failed": row.error}
    row.status, row.external_id, row.post_url, row.posted_at, row.error = "posted", published.external_id[:200], published.url[:500], datetime.now(UTC), ""
    account.status, account.last_error = "ok", ""
    return {"posted": row.id, "url": row.post_url}


def due(session: Session) -> int:
    """Scheduled posts whose time has come and whose task was lost (a restart): queued again."""
    rows = session.execute(select(SocialPost).where(SocialPost.status == "scheduled", SocialPost.scheduled_at <= datetime.now(UTC))).scalars().all()
    for row in rows:
        schedule(session, row)
    return len(rows)


def collect_metrics(session: Session) -> dict[str, int]:
    """What the posts of the last 30 days achieved, and each account's followers."""
    now = datetime.now(UTC)
    rows = session.execute(select(SocialPost).where(SocialPost.status == "posted", SocialPost.posted_at >= now - METRICS_FOR, SocialPost.external_id != "")).scalars().all()
    read = 0
    for row in rows:
        if row.metrics_at and now - row.metrics_at < METRICS_EVERY:
            continue
        account = session.get(SocialAccount, row.account_id) if row.account_id else None
        if account is None:
            continue
        try:
            token = accounts.token(session, account)
            if row.network == "facebook":
                numbers = meta.metrics_facebook(row.external_id, token)
            elif row.network == "instagram":
                numbers = meta.metrics_instagram(row.external_id, token)
            elif row.network == "linkedin":
                numbers = linkedin.metrics(account.external_id, token, row.external_id)
            elif row.network == "youtube":
                numbers = youtube.metrics(token, row.external_id)
            elif row.network == "tiktok":
                numbers = tiktok.metrics(token, row.external_id)
            else:
                continue
        except net.NetworkError:
            continue
        if numbers:
            row.metrics, row.metrics_at = numbers, now
            read += 1
    for account in session.execute(select(SocialAccount).where(SocialAccount.status != "off")).scalars().all():
        if account.followers_at and now - account.followers_at < timedelta(hours=20):
            continue
        try:
            token = accounts.token(session, account)
            count = (meta.followers(account.network, account.external_id, token) if account.network in ("facebook", "instagram")
                     else linkedin.followers(account.external_id, token) if account.network == "linkedin"
                     else tiktok.followers(token) if account.network == "tiktok" else youtube.followers(token))
        except net.NetworkError as exc:
            account.status, account.last_error = "error", str(exc)[:400]
            continue
        if count is not None:
            account.followers, account.followers_at = count, now
    return {"posts": read}


def results(session: Session, days: int = 30) -> dict[str, Any]:
    """Per network and per post: what the posts achieved (for the Marketing Lead, the agents and the admin)."""
    since = datetime.now(UTC) - timedelta(days=days)
    rows = session.execute(select(SocialPost).where(SocialPost.status == "posted", SocialPost.posted_at >= since).order_by(SocialPost.posted_at.desc())).scalars().all()
    per: dict[str, dict[str, int]] = {}
    for row in rows:
        totals = per.setdefault(row.network, {"posts": 0})
        totals["posts"] += 1
        for key, value in (row.metrics or {}).items():
            if isinstance(value, int):
                totals[key] = totals.get(key, 0) + value
    followers = {a.network: a.followers for a in session.execute(select(SocialAccount).where(SocialAccount.status != "off")).scalars()}
    best = sorted(rows, key=lambda r: sum(int(v) for k, v in (r.metrics or {}).items() if k in ("likes", "comments", "shares", "saves", "clicks")), reverse=True)[:8]
    return {"days": days, "followers": followers, "by_network": per,
            "best_posts": [{"id": r.id, "network": r.network, "kind": r.kind, "text": r.text[:200], "posted_at": r.posted_at.isoformat() if r.posted_at else None,
                            "metrics": r.metrics, "url": r.post_url} for r in best],
            "waiting": {s: int(n) for s, n in session.execute(select(SocialPost.status, func.count()).where(SocialPost.status.in_(("scheduled", "needs_video", "ready", "failed")))
                                                               .group_by(SocialPost.status)).all()}}
