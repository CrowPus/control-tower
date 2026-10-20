"""Admin → Office: the HQ, the agent team at work in a 3D office. Reading only."""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from ..services.office import state as office
from .deps import AdminDb, CurrentAdmin

router = APIRouter(prefix="/admin-api/office", tags=["admin-office"])


class OfficeVisit(BaseModel):
    to: str
    about: str
    at: str


class OfficeAgent(BaseModel):
    key: str
    name: str
    department: str
    purpose: str
    mode: str  # working | idle | waiting | off
    doing: str
    trigger: str | None
    since: str | None
    last_run_at: str | None
    visiting: OfficeVisit | None
    runs_today: int
    proposals_today: int
    cost_today_usd: float
    waiting_for_owner: int
    # Outside agents: their desk in the outside wing (1–10) and where they come from.
    desk: int | None = None
    outside: str = ""


class OfficeOwner(BaseModel):
    name: str
    with_agent: str | None


class OfficeHandoff(BaseModel):
    by: str
    to: list[str]
    type: str
    at: str


class OfficeOut(BaseModel):
    now: str
    owner: OfficeOwner
    agents: list[OfficeAgent]
    handoffs: list[OfficeHandoff]


@router.get("", response_model=OfficeOut)
def office_state(_ctx: CurrentAdmin, session: AdminDb) -> OfficeOut:
    """Who is working, idle, waiting or off, who walked over to whom with a handoff, and whom the owner talks to."""
    return OfficeOut.model_validate(office.state(session))
