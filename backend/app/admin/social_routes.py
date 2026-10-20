"""Admin → Marketing: the Marketing Lead's plan, the social media calendar,
the picture studio and media library, the company's character and the videos made from approved concepts
the results, the connected networks and the brand kit.

The owner changes; the team reads. Connecting and disconnecting networks, the apps' secrets, the brand
kit and the budget are in the admin log."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter, File, Query, Request, Response, UploadFile
from fastapi.responses import FileResponse, RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from ..config import settings
from ..core.errors import NotFound, ValidationFailed
from ..core.net import client_ip
from ..models import (
    NETWORKS,
    AdminUser,
    MarketingMedia,
    MarketingPlan,
    MarketingVideo,
    SocialAccount,
    SocialPost,
    TeamApproval,
)
from ..services import platform
from ..services.marketing import brand, character, plans, studio, video_studio
from ..services.marketing.brand import BrandKit, MarketingSettings
from ..services.marketing.character import Character
from ..services.social import accounts, apps, posts
from .auth import record
from .deps import AdminDb, CurrentAdmin, OwnerAdmin

router = APIRouter(prefix="/admin-api/marketing", tags=["admin-marketing"])


class SocialPostOut(BaseModel):
    id: int
    network: str
    kind: str
    text: str
    title: str
    link: str
    image_note: str
    media_ids: list[int]
    video_script: dict[str, Any]
    agent: str
    status: str
    scheduled_at: datetime | None
    post_url: str
    posted_at: datetime | None
    error: str
    metrics: dict[str, Any]
    account: str
    created_at: datetime
    # The network is connected and takes this post: the service can publish it.
    can_publish: bool = False


class SocialAccountOut(BaseModel):
    id: int
    network: str
    # The network's own id (a Facebook Page's id: the Page the ads run as, Marketing → Ads).
    external_id: str
    name: str
    username: str
    picture_url: str
    profile_url: str
    status: str
    last_error: str
    followers: int | None
    followers_at: datetime | None
    token_expires_at: datetime | None
    connected_at: datetime


class MediaOut(BaseModel):
    id: int
    kind: str
    source: str
    title: str
    prompt: str
    width: int
    height: int
    size: int
    link: str
    seconds: float = 0.0
    ai_generated: bool
    shows_people: bool
    alt_text: str
    cost_usd: float
    created_by: str
    created_at: datetime
    url: str


class PlanOut(BaseModel):
    id: int
    title: str
    period_start: str
    period_end: str
    summary: str
    status: str
    goals: list[Any]
    audiences: list[Any]
    campaigns: list[Any]
    briefs: dict[str, Any]
    measures: list[Any]
    agent: str
    created_at: str


class VideoOut(BaseModel):
    id: int
    title: str
    status: str
    concept: dict[str, Any]
    model: str
    uses_character: bool
    scenes_done: int
    scenes: int
    scene_errors: list[str]
    # What the automatic check found when it watched the finished video (empty until it ran).
    review: dict[str, Any]
    media_id: int | None
    seconds: float
    cost_usd: float
    error: str
    agent: str
    created_at: datetime
    made_at: datetime | None
    deleted_at: datetime | None
    posts: list[dict[str, Any]]
    # After a check that found mistakes: the first scene to make again (1-based), and what scenes cost.
    remake_from: int | None = None
    scene_seconds: list[int] = []
    price_per_second: float = 0.0
    # The finished file and its cover picture, for the admin app.
    file_url: str = ""
    cover_url: str = ""


class MarketingOut(BaseModel):
    plan: PlanOut | None
    accounts: list[SocialAccountOut]
    apps: list[apps.AppView]
    networks_by_hand: list[str]
    brand: BrandKit
    settings: MarketingSettings
    budget: dict[str, Any]
    video_budget: dict[str, Any]
    video_rhythm: dict[str, Any]
    character: Character
    waiting: dict[str, int]
    public_media_ready: bool


class BudgetSettingsIn(MarketingSettings):
    pass


class HandNetworksIn(BaseModel):
    networks: list[Literal["facebook", "instagram", "linkedin", "youtube", "tiktok", "x"]]


class ImageIn(BaseModel):
    prompt: str = Field(min_length=15, max_length=4000)
    aspect: str = "4:5"
    headline: str = Field(default="", max_length=90)
    subline: str = Field(default="", max_length=140)
    shows_people: bool = False
    title: str = Field(default="", max_length=200)


class VideoLinkIn(BaseModel):
    link: str = Field(min_length=10, max_length=1000)
    title: str = Field(default="", max_length=200)


class MediaChangeIn(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    alt_text: str | None = Field(default=None, max_length=500)


class PostChangeIn(BaseModel):
    text: str | None = Field(default=None, max_length=5000)
    title: str | None = Field(default=None, max_length=200)
    scheduled_at: datetime | None = None


class PostVideoIn(BaseModel):
    media_id: int | None = None
    link: str = Field(default="", max_length=1000)


class PostedIn(BaseModel):
    post_url: str = Field(default="", max_length=500)


def _post(session: AdminDb, p: SocialPost) -> SocialPostOut:
    account = session.get(SocialAccount, p.account_id) if p.account_id else accounts.connected(session, p.network) if p.network != "x" else None
    can = account is not None and p.kind in posts.CAN.get(p.network, set()) and not (p.network == "instagram" and not p.media_ids) and not (p.kind == "video" and not p.media_ids)
    return SocialPostOut(id=p.id, network=p.network, kind=p.kind, text=p.text, title=p.title, link=p.link, image_note=p.image_note, media_ids=[int(m) for m in p.media_ids or []],
                         video_script=p.video_script or {}, agent=p.agent, status=p.status, scheduled_at=p.scheduled_at, post_url=p.post_url, posted_at=p.posted_at,
                         error=p.error, metrics=p.metrics or {}, account=account.name if account else "", created_at=p.created_at, can_publish=can)


def _media(m: MarketingMedia) -> MediaOut:
    return MediaOut(id=m.id, kind=m.kind, source=m.source, title=m.title, prompt=m.prompt, width=m.width, height=m.height, size=m.size, link=m.link, ai_generated=m.ai_generated,
                    shows_people=m.shows_people, alt_text=m.alt_text, cost_usd=m.cost_usd, created_by=m.created_by, created_at=m.created_at,
                    seconds=m.seconds, url=f"/admin-api/marketing/media/{m.id}/file" if m.kind == "image" else f"/admin-api/marketing/media/{m.id}/video" if m.file else m.link)


def _overview(session: AdminDb) -> MarketingOut:
    plan = plans.active(session)
    rows = session.execute(select(SocialAccount).where(SocialAccount.status != "off").order_by(SocialAccount.network, SocialAccount.id)).scalars()
    hand = [n for n in (platform.get(session, "social") or {}).get("networks") or [] if n in NETWORKS]
    waiting = posts.results(session, 30)["waiting"]
    waiting["video_concepts"] = int(session.execute(select(func.count()).select_from(TeamApproval).where(TeamApproval.action_type == "video.concept",
                                                                                                        TeamApproval.status == "pending")).scalar() or 0)
    return MarketingOut(plan=PlanOut(**plans.view(plan)) if plan else None, accounts=[SocialAccountOut(**accounts.view(a)) for a in rows],
                        apps=[apps.view(session, p) for p in apps.PROVIDERS], networks_by_hand=hand, brand=brand.kit(session), settings=brand.settings_of(session),
                        budget=studio.budget(session), video_budget=video_studio.budget(session), video_rhythm=video_studio.rhythm(session),
                        character=character.get(session), waiting=waiting, public_media_ready=bool(settings().public_base_url))


@router.get("", response_model=MarketingOut)
def overview(_ctx: CurrentAdmin, session: AdminDb) -> MarketingOut:
    return _overview(session)


@router.put("/brand", response_model=MarketingOut)
def put_brand(data: BrandKit, ctx: OwnerAdmin, session: AdminDb) -> MarketingOut:
    if data.logo_media_id is not None:
        logo = session.get(MarketingMedia, data.logo_media_id)
        if logo is None or logo.kind != "image":
            raise ValidationFailed("The logo is a picture in the media library.", code="BRAND_LOGO")
    brand.save_kit(session, data)
    record(session, ctx.admin, "marketing.brand_changed", target_type="marketing", ip=ctx.ip, metadata={"colors": [data.primary_color, data.text_color, data.accent_color]})
    return _overview(session)


@router.put("/settings", response_model=MarketingOut)
def put_settings(data: BudgetSettingsIn, ctx: OwnerAdmin, session: AdminDb) -> MarketingOut:
    before = brand.settings_of(session).model_dump()
    brand.save_settings(session, MarketingSettings.model_validate(data.model_dump()))
    record(session, ctx.admin, "marketing.settings_changed", target_type="marketing", ip=ctx.ip, metadata={"before": before, "after": data.model_dump()})
    return _overview(session)


@router.put("/by-hand", response_model=MarketingOut)
def put_by_hand(data: HandNetworksIn, ctx: OwnerAdmin, session: AdminDb) -> MarketingOut:
    """Networks the owner posts on by hand (not connected, or X)."""
    platform.put(session, "social", {"networks": sorted(set(data.networks))})
    record(session, ctx.admin, "social.networks_changed", target_type="social", ip=ctx.ip, metadata={"networks": data.networks})
    return _overview(session)


# --- connections -------------------------------------------------------------------------------------------
@router.put("/apps/{provider}", response_model=apps.AppView)
def put_app(provider: apps.Provider, data: apps.AppIn, ctx: OwnerAdmin, session: AdminDb) -> apps.AppView:
    out = apps.save(session, provider, data)
    record(session, ctx.admin, "marketing.app_changed", target_type="social_app", target_id=provider, ip=ctx.ip,
           metadata={"client_id": data.client_id, "secret_changed": bool(data.client_secret)})
    return out


@router.post("/connect/{provider}")
def connect(provider: apps.Provider, ctx: OwnerAdmin, session: AdminDb) -> dict[str, str]:
    """The network's sign-in address: the owner's browser goes there and comes back to the callback."""
    return {"url": accounts.start(session, provider, ctx.admin.id)}


@router.get("/connect/{provider}/callback", include_in_schema=False)
def connect_callback(provider: apps.Provider, request: Request, session: AdminDb, code: str = Query(default="", max_length=2000),
                     state: str = Query(default="", max_length=200), error: str = Query(default="", max_length=200),
                     error_description: str = Query(default="", max_length=500)) -> RedirectResponse:
    """The network sends the owner back here. Authorised by the one-time state the owner's Connect made (the
    admin cookie is SameSite=Strict, so it does not come with the network's redirect)."""
    try:
        admin_id = accounts.claim(session, provider, state)
    except ValidationFailed as exc:
        return RedirectResponse(f"/marketing/connections?error={_q(exc.message)}", status_code=303)
    admin = session.get(AdminUser, admin_id)
    if admin is None or admin.role != "owner" or admin.status != "active":
        return RedirectResponse(f"/marketing/connections?error={_q('Only an owner connects networks.')}", status_code=303)
    if error or not code:
        return RedirectResponse(f"/marketing/connections?error={_q(error_description or error or 'The network did not connect.')}", status_code=303)
    try:
        rows = accounts.finish(session, provider, code, admin_id)
    except ValidationFailed as exc:
        return RedirectResponse(f"/marketing/connections?error={_q(exc.message)}", status_code=303)
    record(session, admin, "marketing.connected", target_type="social_app", target_id=provider, ip=client_ip(request),
           metadata={"accounts": [{"network": r.network, "name": r.name} for r in rows]})
    return RedirectResponse(f"/marketing/connections?connected={_q(', '.join(f'{r.network}: {r.name}' for r in rows))}", status_code=303)


def _q(text: str) -> str:
    from urllib.parse import quote

    return quote(text[:300])


@router.delete("/accounts/{account_id}", status_code=204)
def disconnect(account_id: int, ctx: OwnerAdmin, session: AdminDb) -> Response:
    """Disconnect: the tokens are deleted here; the owner removes the app's access at the network too."""
    row = session.get(SocialAccount, account_id)
    if row is None:
        raise NotFound("No such account.", code="SOCIAL_ACCOUNT_NOT_FOUND")
    record(session, ctx.admin, "marketing.disconnected", target_type="social_account", target_id=row.id, ip=ctx.ip, metadata={"network": row.network, "name": row.name})
    for child in session.execute(select(SocialAccount).where(SocialAccount.parent_id == row.id)).scalars():
        session.delete(child)
    session.delete(row)
    return Response(status_code=204)


# --- media -----------------------------------------------------------------------------------------------
@router.get("/media", response_model=list[MediaOut])
def media(_ctx: CurrentAdmin, session: AdminDb, kind: str = Query(default="", max_length=8), limit: int = Query(default=60, ge=1, le=200)) -> list[MediaOut]:
    stmt = select(MarketingMedia).order_by(MarketingMedia.id.desc()).limit(limit)
    if kind in ("image", "video"):
        stmt = stmt.where(MarketingMedia.kind == kind)
    return [_media(m) for m in session.execute(stmt).scalars()]


@router.get("/media/{media_id}/file")
def media_file(media_id: int, _ctx: CurrentAdmin, session: AdminDb) -> Response:
    """A picture, or a made video's cover picture."""
    row = session.get(MarketingMedia, media_id)
    if row is None or not row.data or (row.kind != "image" and not row.file):
        raise NotFound("No such picture.", code="MEDIA_NOT_FOUND")
    mime = row.mime_type if row.kind == "image" else "image/jpeg"
    return Response(content=row.data, media_type=mime, headers={"Cache-Control": "private, max-age=3600", "X-Content-Type-Options": "nosniff",
                                                                 "Content-Disposition": f'inline; filename="media-{row.id}.{"png" if mime == "image/png" else "jpg"}"'})


@router.get("/media/{media_id}/video")
def media_video(media_id: int, _ctx: CurrentAdmin, session: AdminDb) -> FileResponse:
    """A video the studio made, to watch in the admin app."""
    row = session.get(MarketingMedia, media_id)
    path = video_studio.file_of(row) if row is not None and row.kind == "video" else None
    if path is None:
        raise NotFound("No such video (it may have been deleted).", code="MEDIA_NOT_FOUND")
    return FileResponse(path, media_type="video/mp4", filename=f"video-{row.id if row else 0}.mp4", content_disposition_type="inline",
                        headers={"Cache-Control": "private, max-age=600", "X-Content-Type-Options": "nosniff"})


@router.post("/media/image", response_model=MediaOut, status_code=201)
def make_image(data: ImageIn, ctx: OwnerAdmin, session: AdminDb) -> MediaOut:
    """The owner makes a picture in the studio (from the same budget as the agents)."""
    try:
        row = studio.make(session, prompt=data.prompt, aspect=data.aspect, headline=data.headline, subline=data.subline, shows_people=data.shows_people,
                          title=data.title, created_by=f"admin:{ctx.admin.id}")
    except studio.StudioRefused as exc:
        raise ValidationFailed(str(exc), code="STUDIO_REFUSED") from exc
    return _media(row)


@router.post("/media/upload", response_model=MediaOut, status_code=201)
def upload(ctx: OwnerAdmin, session: AdminDb, file: UploadFile = File(...), title: str = Query(default="", max_length=200)) -> MediaOut:  # noqa: B008 - FastAPI's form parameter
    """A real photo or the logo, uploaded by the owner."""
    data = file.file.read(studio.MAX_UPLOAD + 1)
    try:
        row = studio.upload(session, data=data, filename=file.filename or "picture", title=title, created_by=f"admin:{ctx.admin.id}")
    except studio.StudioRefused as exc:
        raise ValidationFailed(str(exc), code="STUDIO_REFUSED") from exc
    return _media(row)


@router.post("/media/video", response_model=MediaOut, status_code=201)
def video_link(data: VideoLinkIn, ctx: OwnerAdmin, session: AdminDb) -> MediaOut:
    try:
        row = studio.link_video(session, link=data.link, title=data.title, created_by=f"admin:{ctx.admin.id}")
    except studio.StudioRefused as exc:
        raise ValidationFailed(str(exc), code="STUDIO_REFUSED") from exc
    return _media(row)


@router.patch("/media/{media_id}", response_model=MediaOut)
def change_media(media_id: int, data: MediaChangeIn, _ctx: OwnerAdmin, session: AdminDb) -> MediaOut:
    row = session.get(MarketingMedia, media_id)
    if row is None:
        raise NotFound("No such media.", code="MEDIA_NOT_FOUND")
    if data.title is not None:
        row.title = data.title
    if data.alt_text is not None:
        row.alt_text = data.alt_text
    return _media(row)


@router.delete("/media/{media_id}", status_code=204)
def delete_media(media_id: int, ctx: OwnerAdmin, session: AdminDb) -> Response:
    row = session.get(MarketingMedia, media_id)
    if row is None:
        raise NotFound("No such media.", code="MEDIA_NOT_FOUND")
    waiting = [p.id for p in session.execute(select(SocialPost).where(SocialPost.status.in_(("scheduled", "needs_video", "ready", "publishing")))).scalars()
               if media_id in (p.media_ids or [])]
    if waiting:
        raise ValidationFailed(f"Posts that are not out yet use it (#{', #'.join(map(str, waiting[:5]))}).", code="MEDIA_IN_USE")
    if brand.kit(session).logo_media_id == media_id:
        raise ValidationFailed("This is the brand's logo; choose another logo first.", code="MEDIA_IS_LOGO")
    if media_id in character.get(session).picture_ids:
        raise ValidationFailed("This is a picture of the company's character; take it out under Character first.", code="MEDIA_IS_CHARACTER")
    made = session.execute(select(MarketingVideo).where(MarketingVideo.media_id == media_id)).scalar_one_or_none()
    if made is not None:
        try:
            video_studio.delete(session, made)
        except video_studio.VideoRefused as exc:
            raise ValidationFailed(str(exc), code="MEDIA_IN_USE") from exc
        record(session, ctx.admin, "marketing.video_deleted", target_type="marketing_video", target_id=made.id, ip=ctx.ip, metadata={"title": made.title})
        return Response(status_code=204)
    video_studio.remove_file(row)
    session.delete(row)
    return Response(status_code=204)


# --- the calendar --------------------------------------------------------------------------------------------
@router.get("/posts", response_model=list[SocialPostOut])
def list_posts(_ctx: CurrentAdmin, session: AdminDb, status: str = Query(default="", max_length=12), limit: int = Query(default=200, ge=1, le=500)) -> list[SocialPostOut]:
    stmt = select(SocialPost).order_by(SocialPost.scheduled_at.desc().nulls_last(), SocialPost.id.desc()).limit(limit)
    if status:
        stmt = stmt.where(SocialPost.status == status)
    return [_post(session, p) for p in session.execute(stmt).scalars()]


def _editable(session: AdminDb, post_id: int, *states: str) -> SocialPost:
    row = session.get(SocialPost, post_id)
    if row is None:
        raise NotFound("No such post.", code="POST_NOT_FOUND")
    if states and row.status not in states:
        raise ValidationFailed("This post cannot be changed any more.", code="POST_HANDLED")
    return row


@router.patch("/posts/{post_id}", response_model=SocialPostOut)
def change_post(post_id: int, data: PostChangeIn, _ctx: OwnerAdmin, session: AdminDb) -> SocialPostOut:
    row = _editable(session, post_id, "scheduled", "needs_video", "ready", "failed")
    if data.text is not None:
        if not data.text.strip() and row.network != "youtube":
            raise ValidationFailed("The text is empty.", code="POST_EMPTY")
        if len(data.text) > posts.LIMITS[row.network]:
            raise ValidationFailed(f"Too long for {row.network}: {len(data.text)} of {posts.LIMITS[row.network]} characters.", code="POST_TOO_LONG")
        row.text = data.text
    if data.title is not None:
        row.title = data.title
    if data.scheduled_at is not None:
        when = data.scheduled_at if data.scheduled_at.tzinfo else data.scheduled_at.replace(tzinfo=UTC)
        row.scheduled_at = when.astimezone(UTC)
        if row.status == "scheduled":
            posts.schedule(session, row)
    return _post(session, row)


@router.post("/posts/{post_id}/video", response_model=SocialPostOut)
def add_video(post_id: int, data: PostVideoIn, ctx: OwnerAdmin, session: AdminDb) -> SocialPostOut:
    """The owner's video for a video post: from the media library or by its link; then it is scheduled."""
    row = _editable(session, post_id, "needs_video", "failed", "scheduled")
    if data.media_id:
        clip = session.get(MarketingMedia, data.media_id)
        if clip is None or clip.kind != "video":
            raise ValidationFailed("Choose a video from the media library.", code="POST_VIDEO")
    else:
        try:
            clip = studio.link_video(session, link=data.link, title=row.title or f"Video for post {row.id}", created_by=f"admin:{ctx.admin.id}")
        except studio.StudioRefused as exc:
            raise ValidationFailed(str(exc), code="POST_VIDEO") from exc
    row.media_ids = [clip.id]
    account = accounts.connected(session, row.network)
    if account is None:
        row.status = "ready"
    else:
        row.account_id, row.status, row.error = account.id, "scheduled", ""
        if not row.scheduled_at or row.scheduled_at < datetime.now(UTC):
            row.scheduled_at = datetime.now(UTC)
        posts.schedule(session, row)
    return _post(session, row)


@router.post("/posts/{post_id}/publish-now", response_model=SocialPostOut)
def publish_now(post_id: int, _ctx: OwnerAdmin, session: AdminDb) -> SocialPostOut:
    """Publish at once — also a post that waited to be posted by hand, once its network is connected."""
    row = _editable(session, post_id, "scheduled", "failed", "ready")
    if row.kind == "video" and not row.media_ids:
        raise ValidationFailed("Add the video first.", code="POST_VIDEO")
    account = accounts.connected(session, row.network)
    if account is None:
        raise ValidationFailed(f"{row.network} is not connected; post it by hand.", code="POST_NOT_CONNECTED")
    if row.kind not in posts.CAN.get(row.network, set()) or (row.network == "instagram" and not row.media_ids):
        raise ValidationFailed(f"{row.network} does not take this post as it is ({row.kind}); Instagram needs a picture or a video.", code="POST_KIND")
    row.account_id, row.status, row.error, row.scheduled_at = account.id, "scheduled", "", datetime.now(UTC)
    posts.schedule(session, row)
    return _post(session, row)


@router.post("/posts/{post_id}/posted", response_model=SocialPostOut)
def posted_by_hand(post_id: int, data: PostedIn, ctx: OwnerAdmin, session: AdminDb) -> SocialPostOut:
    row = _editable(session, post_id, "ready", "failed", "needs_video")
    if data.post_url and not data.post_url.startswith("https://"):
        raise ValidationFailed("The address starts with https://.", code="POST_URL")
    row.status, row.post_url, row.posted_at, row.error = "posted", data.post_url, datetime.now(UTC), ""
    record(session, ctx.admin, "social.posted", target_type="social_post", target_id=row.id, ip=ctx.ip)
    return _post(session, row)


@router.post("/posts/{post_id}/discard", response_model=SocialPostOut)
def discard(post_id: int, ctx: OwnerAdmin, session: AdminDb) -> SocialPostOut:
    row = _editable(session, post_id, "ready", "scheduled", "needs_video", "failed")
    row.status = "discarded"
    record(session, ctx.admin, "social.discarded", target_type="social_post", target_id=row.id, ip=ctx.ip)
    return _post(session, row)


# --- results and plans ----------------------------------------------------------------------------------------------
@router.get("/results")
def results(_ctx: CurrentAdmin, session: AdminDb, days: int = Query(default=30, ge=1, le=365)) -> dict[str, Any]:
    return posts.results(session, days)


@router.get("/plans", response_model=list[PlanOut])
def list_plans(_ctx: CurrentAdmin, session: AdminDb) -> list[PlanOut]:
    rows = session.execute(select(MarketingPlan).order_by(MarketingPlan.id.desc()).limit(24)).scalars()
    return [PlanOut(**plans.view(p)) for p in rows]


# --- the character and the videos ------------------------------------------------------
@router.get("/character", response_model=Character)
def get_character(_ctx: CurrentAdmin, session: AdminDb) -> Character:
    return character.get(session)


@router.put("/character", response_model=Character)
def put_character(data: Character, ctx: OwnerAdmin, session: AdminDb) -> Character:
    """The owner's character: its description and its pictures (from the media library, the best first; at most three)."""
    try:
        saved = character.save(session, data)
    except ValueError as exc:
        raise ValidationFailed(str(exc), code="CHARACTER_PICTURES") from exc
    record(session, ctx.admin, "marketing.character_changed", target_type="marketing", ip=ctx.ip, metadata={"name": saved.name, "pictures": saved.picture_ids})
    return saved


def _video(session: AdminDb, row: MarketingVideo) -> VideoOut:
    seen = video_studio.view(session, row)
    seen.pop("public_token", None)
    media_id = seen.get("media_id")
    return VideoOut(**seen, file_url=f"/admin-api/marketing/media/{media_id}/video" if media_id else "",
                    cover_url=f"/admin-api/marketing/media/{media_id}/file" if media_id else "")


@router.get("/videos", response_model=list[VideoOut])
def list_videos(_ctx: CurrentAdmin, session: AdminDb, limit: int = Query(default=60, ge=1, le=200)) -> list[VideoOut]:
    rows = session.execute(select(MarketingVideo).order_by(MarketingVideo.id.desc()).limit(limit)).scalars()
    return [_video(session, r) for r in rows]


def _made(session: AdminDb, video_id: int) -> MarketingVideo:
    row = session.get(MarketingVideo, video_id)
    if row is None:
        raise NotFound("No such video.", code="VIDEO_NOT_FOUND")
    return row


@router.post("/videos/{video_id}/again", response_model=VideoOut)
def video_again(video_id: int, ctx: OwnerAdmin, session: AdminDb) -> VideoOut:
    """Try a failed video again: the scenes already made are kept (and not paid again)."""
    row = _made(session, video_id)
    try:
        video_studio.again(session, row)
    except video_studio.VideoRefused as exc:
        raise ValidationFailed(str(exc), code="VIDEO_STATE") from exc
    record(session, ctx.admin, "marketing.video_again", target_type="marketing_video", target_id=row.id, ip=ctx.ip)
    return _video(session, row)


class RemakeIn(BaseModel):
    from_scene: int = Field(ge=1, le=10)


@router.post("/videos/{video_id}/remake", response_model=VideoOut)
def video_remake(video_id: int, data: RemakeIn, ctx: OwnerAdmin, session: AdminDb) -> VideoOut:
    """Make a finished video's scenes again from one scene on, with what the check found told to the video model; the scenes
    before are kept and not paid again."""
    row = _made(session, video_id)
    try:
        cost = video_studio.remake(session, row, data.from_scene)
    except video_studio.VideoRefused as exc:
        raise ValidationFailed(str(exc), code="VIDEO_STATE") from exc
    record(session, ctx.admin, "marketing.video_remade", target_type="marketing_video", target_id=row.id, ip=ctx.ip, metadata={"from_scene": data.from_scene, "cost_usd": cost})
    return _video(session, row)


@router.delete("/videos/{video_id}", response_model=VideoOut)
def delete_video(video_id: int, ctx: OwnerAdmin, session: AdminDb) -> VideoOut:
    """Delete a video's file (usually once it is posted). The posts on the networks stay; the concept stays for the record."""
    row = _made(session, video_id)
    try:
        video_studio.delete(session, row)
    except video_studio.VideoRefused as exc:
        raise ValidationFailed(str(exc), code="VIDEO_IN_USE") from exc
    record(session, ctx.admin, "marketing.video_deleted", target_type="marketing_video", target_id=row.id, ip=ctx.ip, metadata={"title": row.title})
    return _video(session, row)
