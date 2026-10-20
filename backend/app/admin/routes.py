"""Admin API (`/admin-api`), English only: sign-in, the overview, tasks, settings,
schedules, AI usage, system health, the admin log and admin accounts. Every change
is written to the admin log.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import func, select, update

from ..config import settings
from ..core.errors import Forbidden, NotFound, ValidationFailed
from ..core.net import client_ip
from ..models import (
    AdminAuditLog,
    AdminSession,
    AdminUser,
    AgentTask,
    AIUsage,
    ScheduledJob,
)
from ..services import (
    bells,
    platform,
    system_mail,
)
from . import auth
from .deps import AdminDb, CurrentAdmin, OwnerAdmin, PendingAdmin

router = APIRouter(prefix="/admin-api", tags=["admin"])


class OkOut(BaseModel):
    ok: bool = True
    message: str = ""


# --- sign-in ------------------------------------------------------------------------------
class LoginIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=256)


class CodeIn(BaseModel):
    code: str = Field(min_length=6, max_length=12)


class AcceptInviteIn(BaseModel):
    token: str = Field(min_length=1, max_length=200)
    password: str = Field(min_length=12, max_length=256)


class PasswordChangeIn(BaseModel):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=12, max_length=256)


class AdminOut(BaseModel):
    id: int
    email: str
    full_name: str
    role: str
    status: str
    mfa_enrolled: bool
    last_login_at: datetime | None
    created_at: datetime


class MeOut(BaseModel):
    admin: AdminOut
    mfa_passed: bool
    csrf_token: str


class LoginOut(BaseModel):
    next: Literal["verify", "enroll"]
    csrf_token: str


class EnrolmentOut(BaseModel):
    secret: str
    otpauth_uri: str
    qr_svg: str


def _admin_out(a: AdminUser) -> AdminOut:
    return AdminOut(id=a.id, email=a.email, full_name=a.full_name, role=a.role, status=a.status, mfa_enrolled=a.totp_enabled_at is not None, last_login_at=a.last_login_at, created_at=a.created_at)


def _set_cookie(response: Response, raw: str) -> None:
    cfg = settings()
    response.set_cookie(
        key=cfg.admin_session_cookie_name,
        value=raw,
        httponly=True,
        secure=cfg.cookie_secure,
        samesite="strict",
        path="/",
        max_age=cfg.admin_session_hours * 3600,
    )


@router.post("/auth/login", response_model=LoginOut)
def login(data: LoginIn, request: Request, response: Response, session: AdminDb) -> LoginOut:
    admin, row, raw = auth.login(session, email=str(data.email), password=data.password, ip=client_ip(request), user_agent=request.headers.get("user-agent", ""))
    _set_cookie(response, raw)
    return LoginOut(next="verify" if admin.totp_enabled_at else "enroll", csrf_token=row.csrf_token)


@router.post("/auth/enroll/start", response_model=EnrolmentOut)
def enroll_start(ctx: PendingAdmin, session: AdminDb) -> EnrolmentOut:
    return EnrolmentOut(**auth.start_enrolment(session, ctx.admin))


@router.post("/auth/enroll/confirm", response_model=MeOut)
def enroll_confirm(data: CodeIn, ctx: PendingAdmin, session: AdminDb) -> MeOut:
    auth.check_code(session, ctx.admin, ctx.session, data.code, enrolling=True, ip=ctx.ip)
    return MeOut(admin=_admin_out(ctx.admin), mfa_passed=True, csrf_token=ctx.session.csrf_token)


@router.post("/auth/verify", response_model=MeOut)
def verify(data: CodeIn, ctx: PendingAdmin, session: AdminDb) -> MeOut:
    auth.check_code(session, ctx.admin, ctx.session, data.code, enrolling=False, ip=ctx.ip)
    return MeOut(admin=_admin_out(ctx.admin), mfa_passed=True, csrf_token=ctx.session.csrf_token)


@router.get("/auth/me", response_model=MeOut)
def me(ctx: PendingAdmin) -> MeOut:
    return MeOut(admin=_admin_out(ctx.admin), mfa_passed=ctx.session.mfa_passed, csrf_token=ctx.session.csrf_token)


@router.post("/auth/logout", response_model=OkOut)
def logout(ctx: PendingAdmin, response: Response, session: AdminDb) -> OkOut:
    auth.logout(session, ctx.session, ctx.admin, ctx.ip)
    response.delete_cookie(settings().admin_session_cookie_name, path="/")
    return OkOut()


@router.post("/auth/password", response_model=OkOut)
def change_password(data: PasswordChangeIn, ctx: CurrentAdmin, session: AdminDb) -> OkOut:
    """Signed in with password and authenticator code: change one's own password."""
    auth.change_password(session, ctx.admin, ctx.session, old=data.current_password, new=data.new_password, ip=ctx.ip)
    return OkOut(message="Password changed. Your other sign-ins were ended; this one stays.")


@router.post("/auth/accept-invite", response_model=OkOut)
def accept_invite(data: AcceptInviteIn, session: AdminDb) -> OkOut:
    auth.accept_invite(session, token=data.token, password=data.password)
    return OkOut(message="Password set. Sign in to set up your authenticator app.")


# --- overview -------------------------------------------------------------------------------
class OverviewOut(BaseModel):
    agents_total: int
    agents_on: int
    runs_today: int
    proposals_waiting: int
    ai_spend_today_usd: float
    ai_spend_month_usd: float
    ai_cap_daily_usd: float
    ai_cap_monthly_usd: float
    tasks_failed_24h: int
    tasks_dead: int
    tasks_queued: int
    last_task_finished_at: datetime | None
    switches: dict[str, bool]


def _count(session: Any, stmt: Any) -> int:
    return int(session.execute(stmt).scalar() or 0)


@router.get("/overview", response_model=OverviewOut)
def overview(ctx: CurrentAdmin, session: AdminDb) -> OverviewOut:
    from ..models import TeamApproval, TeamRun
    from ..services.team import config as team_config
    from ..services.team import registry

    cfg = settings()
    now = datetime.now(UTC)
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    month_start = day_start.replace(day=1)

    def spend(since: datetime) -> float:
        return float(session.execute(select(func.coalesce(func.sum(AIUsage.estimated_cost_usd), 0.0)).where(AIUsage.created_at >= since)).scalar() or 0.0)

    keys = list(registry.AGENTS)
    return OverviewOut(
        agents_total=len(keys),
        agents_on=sum(1 for key in keys if team_config.agent(session, key).enabled),
        runs_today=_count(session, select(func.count(TeamRun.id)).where(TeamRun.started_at >= day_start)),
        proposals_waiting=_count(session, select(func.count(TeamApproval.id)).where(TeamApproval.status == "pending")),
        ai_spend_today_usd=round(spend(day_start), 4),
        ai_spend_month_usd=round(spend(month_start), 4),
        ai_cap_daily_usd=cfg.ai_daily_budget_usd,
        ai_cap_monthly_usd=cfg.ai_monthly_budget_usd,
        tasks_failed_24h=_count(session, select(func.count(AgentTask.id)).where(AgentTask.status == "failed", AgentTask.updated_at >= now - timedelta(hours=24))),
        tasks_dead=_count(session, select(func.count(AgentTask.id)).where(AgentTask.status == "dead")),
        tasks_queued=_count(session, select(func.count(AgentTask.id)).where(AgentTask.status == "queued")),
        last_task_finished_at=session.execute(select(func.max(AgentTask.finished_at))).scalar(),
        switches=platform.switches(session),
    )


# --- tasks ---------------------------------------------------------------------------------------
class TaskRow(BaseModel):
    id: int
    queue: str
    task_type: str
    status: str
    attempts: int
    max_attempts: int
    run_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    last_error: str
    last_error_code: str
    created_at: datetime


class TaskPage(BaseModel):
    items: list[TaskRow]
    total: int
    page: int
    page_size: int
    depths: dict[str, dict[str, int]]


@router.get("/tasks", response_model=TaskPage)
def list_tasks(ctx: CurrentAdmin, session: AdminDb, status: str | None = None, queue: str | None = None, page: int = 1, page_size: int = 50) -> TaskPage:
    page = max(page, 1)
    page_size = min(max(page_size, 1), 200)
    cols = (AgentTask.id, AgentTask.queue, AgentTask.task_type, AgentTask.status, AgentTask.attempts, AgentTask.max_attempts, AgentTask.run_at, AgentTask.started_at,
            AgentTask.finished_at, AgentTask.last_error, AgentTask.last_error_code, AgentTask.created_at)
    stmt = select(*cols)
    if status:
        stmt = stmt.where(AgentTask.status == status)
    if queue:
        stmt = stmt.where(AgentTask.queue == queue)
    total = _count(session, select(func.count()).select_from(stmt.subquery()))
    rows = session.execute(stmt.order_by(AgentTask.created_at.desc()).offset((page - 1) * page_size).limit(page_size)).all()
    depths: dict[str, dict[str, int]] = {}
    for queue_name, state, n in session.execute(select(AgentTask.queue, AgentTask.status, func.count(AgentTask.id)).group_by(AgentTask.queue, AgentTask.status)).all():
        depths.setdefault(queue_name, {})[state] = int(n)
    return TaskPage(
        items=[TaskRow(id=r[0], queue=r[1], task_type=r[2], status=r[3], attempts=r[4], max_attempts=r[5], run_at=r[6], started_at=r[7], finished_at=r[8],
                       last_error=(r[9] or "")[:500], last_error_code=r[10] or "", created_at=r[11]) for r in rows],
        total=total,
        page=page,
        page_size=page_size,
        depths=depths,
    )


def _task_status(session: Any, task_id: int) -> str:
    status = session.execute(select(AgentTask.status).where(AgentTask.id == task_id)).scalar_one_or_none()
    if status is None:
        raise NotFound("Task not found.")
    return str(status)


@router.post("/tasks/{task_id}/retry", response_model=OkOut)
def retry_task(task_id: int, ctx: CurrentAdmin, session: AdminDb) -> OkOut:
    if _task_status(session, task_id) not in ("failed", "dead"):
        raise ValidationFailed("Only failed or dead tasks can be retried.", code="TASK_NOT_RETRYABLE")
    now = datetime.now(UTC)
    session.execute(update(AgentTask).where(AgentTask.id == task_id).values(status="queued", attempts=0, run_at=now, locked_by="", last_error="", last_error_code="", finished_at=None, updated_at=now))
    auth.record(session, ctx.admin, "task.retried", target_type="task", target_id=task_id, ip=ctx.ip)
    return OkOut(message="Queued again.")


@router.post("/tasks/{task_id}/cancel", response_model=OkOut)
def cancel_task(task_id: int, ctx: CurrentAdmin, session: AdminDb) -> OkOut:
    if _task_status(session, task_id) not in ("queued", "failed", "dead"):
        raise ValidationFailed("Only queued, failed or dead tasks can be cancelled.", code="TASK_NOT_CANCELLABLE")
    now = datetime.now(UTC)
    session.execute(update(AgentTask).where(AgentTask.id == task_id).values(status="cancelled", finished_at=now, dedupe_key=None, updated_at=now))
    auth.record(session, ctx.admin, "task.cancelled", target_type="task", target_id=task_id, ip=ctx.ip)
    return OkOut(message="Cancelled.")


# --- platform settings -----------------------------------------------------------------------------
class PlatformOut(BaseModel):
    maintenance_banner: str
    switches: dict[str, bool]
    server_ai_cap_daily_usd: float
    server_ai_cap_monthly_usd: float
    system_mail_configured: bool
    public_base_url: str


class PlatformIn(BaseModel):
    maintenance_banner: str | None = Field(default=None, max_length=300)
    switches: dict[str, bool] | None = None


def _platform_out(session: Any) -> PlatformOut:
    cfg = settings()
    values = platform.get_all(session)
    return PlatformOut(
        maintenance_banner=str(values["maintenance_banner"] or ""),
        switches=platform.switches(session),
        server_ai_cap_daily_usd=cfg.ai_daily_budget_usd,
        server_ai_cap_monthly_usd=cfg.ai_monthly_budget_usd,
        system_mail_configured=system_mail.is_configured(),
        public_base_url=cfg.public_base_url,
    )


@router.get("/platform", response_model=PlatformOut)
def get_platform(ctx: CurrentAdmin, session: AdminDb) -> PlatformOut:
    return _platform_out(session)


@router.put("/platform", response_model=PlatformOut)
def put_platform(data: PlatformIn, ctx: OwnerAdmin, session: AdminDb) -> PlatformOut:
    changes = data.model_dump(exclude_none=True)
    if not changes:
        raise ValidationFailed("Nothing to change.", code="NO_CHANGES")
    before = _platform_out(session).model_dump()
    if "switches" in changes:
        unknown = set(changes["switches"]) - set(platform.SWITCHES)
        if unknown:
            raise ValidationFailed(f"Unknown switch: {', '.join(sorted(unknown))}", code="UNKNOWN_SWITCH")
        changes["switches"] = {**platform.switches(session), **changes["switches"]}
    for key, value in changes.items():
        platform.put(session, key, value)
    session.flush()
    auth.record(session, ctx.admin, "platform.changed", target_type="platform", ip=ctx.ip, metadata={"before": {k: before.get(k) for k in changes}, "after": changes})
    return _platform_out(session)


# --- schedules and platform policies ---------------------------------------------------------------
class JobOut(BaseModel):
    id: int
    name: str
    task_type: str
    queue: str
    interval_seconds: int
    enabled: bool
    last_run_at: datetime | None
    next_run_at: datetime | None
    last_status: str


class JobPatch(BaseModel):
    enabled: bool | None = None
    interval_seconds: int | None = Field(default=None, ge=300, le=7 * 24 * 3600)


def _job_out(j: ScheduledJob) -> JobOut:
    return JobOut(id=j.id, name=j.name, task_type=j.task_type, queue=j.queue, interval_seconds=j.interval_seconds, enabled=j.enabled, last_run_at=j.last_run_at, next_run_at=j.next_run_at, last_status=j.last_status or "")


class DeliveryRow(BaseModel):
    id: int
    kind: str
    owner: str
    recipient: str
    status: str
    attempts: int
    last_error: str
    bounce_reason: str
    bounce_detail: str
    created_at: datetime
    sent_at: datetime | None
    settled_at: datetime | None


class DeliveriesOut(BaseModel):
    counts: dict[str, int]
    items: list[DeliveryRow]


@router.get("/email-deliveries", response_model=DeliveriesOut)
def email_deliveries(ctx: CurrentAdmin, session: AdminDb, status: str = "problems", limit: int = 100) -> DeliveriesOut:
    """The install's e-mails of the last 30 days: what failed or came back (default),
    what waits for a retry, or everything. Never their content."""
    from ..models import EmailDelivery as D

    since = datetime.now(UTC) - timedelta(days=30)
    columns = (D.id, D.kind, D.owner, D.recipient, D.status, D.attempts, D.last_error, D.bounce_reason, D.bounce_detail, D.created_at, D.sent_at, D.settled_at)
    stmt = select(*columns).where(D.created_at >= since)
    wanted = {"problems": ("failed", "bounced"), "retrying": ("retrying",), "bounced": ("bounced",), "failed": ("failed",)}.get(status)
    if wanted:
        stmt = stmt.where(D.status.in_(wanted))
    rows = session.execute(stmt.order_by(D.id.desc()).limit(max(1, min(limit, 500)))).all()
    counts = {str(k): int(v) for k, v in session.execute(select(D.status, func.count(D.id)).where(D.created_at >= since).group_by(D.status)).all()}
    return DeliveriesOut(counts=counts, items=[DeliveryRow(**r._mapping) for r in rows])


@router.get("/schedules", response_model=list[JobOut])
def list_schedules(ctx: CurrentAdmin, session: AdminDb) -> list[JobOut]:
    return [_job_out(j) for j in session.execute(select(ScheduledJob).order_by(ScheduledJob.name)).scalars()]


@router.patch("/schedules/{job_id}", response_model=JobOut)
def patch_schedule(job_id: int, data: JobPatch, ctx: OwnerAdmin, session: AdminDb) -> JobOut:
    job = session.get(ScheduledJob, job_id)
    if job is None:
        raise NotFound("Job not found.")
    changes = data.model_dump(exclude_none=True)
    for key, value in changes.items():
        setattr(job, key, value)
    job.updated_at = datetime.now(UTC)
    auth.record(session, ctx.admin, "schedule.changed", target_type="scheduled_job", target_id=job.name, ip=ctx.ip, metadata=changes)
    return _job_out(job)


# --- AI usage --------------------------------------------------------------------------------------
class AIUsageOut(BaseModel):
    days: list[dict[str, Any]]
    by_model: list[dict[str, Any]]
    by_operation: list[dict[str, Any]]


@router.get("/ai-usage", response_model=AIUsageOut)
def ai_usage(ctx: CurrentAdmin, session: AdminDb, days: int = 30) -> AIUsageOut:
    days = min(max(days, 1), 180)
    since = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=days - 1)
    per_day: dict[str, dict[str, Any]] = {}
    for created, cost, tokens_in, tokens_out in session.execute(select(AIUsage.created_at, AIUsage.estimated_cost_usd, AIUsage.input_tokens, AIUsage.output_tokens).where(AIUsage.created_at >= since)).all():
        bucket = per_day.setdefault(created.date().isoformat(), {"day": created.date().isoformat(), "cost_usd": 0.0, "calls": 0, "input_tokens": 0, "output_tokens": 0})
        bucket["cost_usd"] += float(cost or 0)
        bucket["calls"] += 1
        bucket["input_tokens"] += int(tokens_in or 0)
        bucket["output_tokens"] += int(tokens_out or 0)
    cost = func.coalesce(func.sum(AIUsage.estimated_cost_usd), 0.0)
    by_model = [{"model": m, "calls": int(c), "cost_usd": round(float(s), 4)} for m, c, s in session.execute(
        select(AIUsage.model, func.count(AIUsage.id), cost).where(AIUsage.created_at >= since).group_by(AIUsage.model).order_by(func.count(AIUsage.id).desc())).all()]
    by_operation = [{"operation": o, "calls": int(c), "cost_usd": round(float(s), 4)} for o, c, s in session.execute(
        select(AIUsage.operation, func.count(AIUsage.id), cost).where(AIUsage.created_at >= since).group_by(AIUsage.operation).order_by(cost.desc()).limit(30)).all()]
    for bucket in per_day.values():
        bucket["cost_usd"] = round(bucket["cost_usd"], 4)
    return AIUsageOut(days=[per_day[k] for k in sorted(per_day)], by_model=by_model, by_operation=by_operation)


# --- system health -----------------------------------------------------------------------------------
class SystemOut(BaseModel):
    database: str
    database_engine: str
    execution_mode: str
    queues: dict[str, dict[str, int]]
    oldest_queued_at: datetime | None
    last_task_finished_at: datetime | None
    scheduler_last_run_at: datetime | None
    system_mail_configured: bool
    ai_configured: bool
    redis_configured: bool


@router.get("/system", response_model=SystemOut)
def system(ctx: CurrentAdmin, session: AdminDb) -> SystemOut:
    cfg = settings()
    depths: dict[str, dict[str, int]] = {}
    for queue_name, state, n in session.execute(select(AgentTask.queue, AgentTask.status, func.count(AgentTask.id)).group_by(AgentTask.queue, AgentTask.status)).all():
        depths.setdefault(queue_name, {})[state] = int(n)
    return SystemOut(
        database="ok",
        database_engine=cfg.database_engine,
        execution_mode=cfg.execution_mode,
        queues=depths,
        oldest_queued_at=session.execute(select(func.min(AgentTask.run_at)).where(AgentTask.status == "queued")).scalar(),
        last_task_finished_at=session.execute(select(func.max(AgentTask.finished_at))).scalar(),
        scheduler_last_run_at=session.execute(select(func.max(ScheduledJob.last_run_at))).scalar(),
        system_mail_configured=system_mail.is_configured(),
        ai_configured=bool(cfg.gemini_api_key),
        redis_configured=bool(cfg.redis_url),
    )


# --- the admin log -----------------------------------------------------------------------------------
class AdminLogRow(BaseModel):
    id: int
    timestamp: datetime
    admin_email: str
    action: str
    target_type: str
    target_id: str
    ip_address: str
    result: str
    metadata: dict[str, Any]


class AdminLogPage(BaseModel):
    items: list[AdminLogRow]
    total: int
    page: int
    page_size: int


@router.get("/audit", response_model=AdminLogPage)
def admin_log(ctx: CurrentAdmin, session: AdminDb, action: str | None = None, admin_email: str | None = None, page: int = 1, page_size: int = 50) -> AdminLogPage:
    page = max(page, 1)
    page_size = min(max(page_size, 1), 200)
    stmt = select(AdminAuditLog)
    if action:
        stmt = stmt.where(AdminAuditLog.action.like(f"{action}%"))
    if admin_email:
        stmt = stmt.where(AdminAuditLog.admin_email == admin_email.lower())
    total = _count(session, select(func.count()).select_from(stmt.subquery()))
    rows = session.execute(stmt.order_by(AdminAuditLog.timestamp.desc(), AdminAuditLog.id.desc()).offset((page - 1) * page_size).limit(page_size)).scalars()
    return AdminLogPage(items=[AdminLogRow(id=r.id, timestamp=r.timestamp, admin_email=r.admin_email, action=r.action, target_type=r.target_type, target_id=r.target_id, ip_address=r.ip_address, result=r.result, metadata=r.metadata_ or {}) for r in rows], total=total, page=page, page_size=page_size)


# --- admins (owner only) -------------------------------------------------------------------------------
class AdminInviteIn(BaseModel):
    email: EmailStr
    full_name: str = Field(min_length=1, max_length=200)
    role: Literal["owner", "support"] = "support"


class AdminInvited(BaseModel):
    admin: AdminOut
    link: str
    emailed: bool


class AdminPatch(BaseModel):
    role: Literal["owner", "support"] | None = None
    status: Literal["active", "disabled"] | None = None


@router.get("/admins", response_model=list[AdminOut])
def list_admins(ctx: OwnerAdmin, session: AdminDb) -> list[AdminOut]:
    return [_admin_out(a) for a in session.execute(select(AdminUser).order_by(AdminUser.created_at)).scalars()]


@router.post("/admins", response_model=AdminInvited, status_code=201)
def invite_admin(data: AdminInviteIn, ctx: OwnerAdmin, session: AdminDb) -> AdminInvited:
    admin, raw = auth.invite(session, email=str(data.email), full_name=data.full_name, role=data.role, invited_by=ctx.admin)
    link = auth.invite_link(raw)
    emailed = True
    try:
        system_mail.send("admin_invitation", to=admin.email, name=admin.full_name, locale="en", link=link, session=session, owner=f"admin:{admin.id}")
    except Exception:  # noqa: BLE001 - the link is shown to the owner either way
        emailed = False
    auth.record(session, ctx.admin, "admin.invited", target_type="admin", target_id=admin.id, ip=ctx.ip, metadata={"email": admin.email, "role": admin.role})
    return AdminInvited(admin=_admin_out(admin), link=link, emailed=emailed)


def _other_admin(session: Any, ctx: Any, admin_id: int) -> AdminUser:
    target: AdminUser | None = session.get(AdminUser, admin_id)
    if target is None:
        raise NotFound("Admin not found.")
    if target.id == ctx.admin.id:
        raise Forbidden("Ask another owner to change your own account.", code="SELF_CHANGE")
    return target


@router.patch("/admins/{admin_id}", response_model=AdminOut)
def patch_admin(admin_id: int, data: AdminPatch, ctx: OwnerAdmin, session: AdminDb) -> AdminOut:
    target = _other_admin(session, ctx, admin_id)
    changes = data.model_dump(exclude_none=True)
    for key, value in changes.items():
        setattr(target, key, value)
    target.updated_at = datetime.now(UTC)
    if data.status == "disabled":
        session.execute(update(AdminSession).where(AdminSession.admin_user_id == target.id, AdminSession.revoked_at.is_(None)).values(revoked_at=datetime.now(UTC)))
    auth.record(session, ctx.admin, "admin.changed", target_type="admin", target_id=target.id, ip=ctx.ip, metadata=changes)
    return _admin_out(target)


@router.post("/admins/{admin_id}/reset-mfa", response_model=OkOut)
def reset_admin_mfa(admin_id: int, ctx: OwnerAdmin, session: AdminDb) -> OkOut:
    target = _other_admin(session, ctx, admin_id)
    auth.reset_mfa(session, target)
    auth.record(session, ctx.admin, "admin.mfa_reset", target_type="admin", target_id=target.id, ip=ctx.ip)
    return OkOut(message="Authenticator reset. They set up a new one at their next sign-in.")




# --- the notification bell --------------------------------------------------------------------------
class BellItemOut(BaseModel):
    id: str
    kind: str
    at: datetime
    unread: bool
    link: str
    params: dict[str, str]


class BellOut(BaseModel):
    unread: int
    items: list[BellItemOut]


def _bell(feed: bells.Feed) -> BellOut:
    return BellOut(unread=feed.unread, items=[BellItemOut(id=i.id, kind=i.kind, at=i.at, unread=i.unread, link=i.link, params=i.params) for i in feed.items])


@router.get("/notifications", response_model=BellOut)
def notifications(ctx: CurrentAdmin, session: AdminDb) -> BellOut:
    return _bell(bells.admin_feed(session, ctx.admin))


@router.post("/notifications/seen", response_model=OkOut)
def notifications_seen(ctx: CurrentAdmin, session: AdminDb) -> OkOut:
    admin = session.get(AdminUser, ctx.admin.id)
    if admin is not None:
        bells.admin_seen(session, admin)
    return OkOut()
