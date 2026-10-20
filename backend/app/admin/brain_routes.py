"""Admin → Brain: the agent team's shared knowledge as one graph the owner explores
and asks questions of. Reading only; the answers cost from Nia's AI budget."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from ..core.errors import ValidationFailed
from ..db import admin_transaction
from ..services.brain import ask as brain_ask
from ..services.brain import graph as brain
from .auth import record
from .deps import AdminDb, CurrentAdmin, OwnerAdminBrief

router = APIRouter(prefix="/admin-api/brain", tags=["admin-brain"])


class BrainNode(BaseModel):
    id: str
    kind: str
    label: str
    text: str
    at: str | None
    degree: int
    detail: dict[str, Any]


class BrainLink(BaseModel):
    source: str
    target: str
    kind: str
    weight: int


class BrainOut(BaseModel):
    nodes: list[BrainNode]
    links: list[BrainLink]
    counts: dict[str, int]


class BrainAskIn(BaseModel):
    question: str = Field(min_length=1, max_length=1000)
    days: int | None = Field(default=None, ge=1, le=3650)


class BrainAnswerOut(BaseModel):
    text: str
    cited: list[str]
    found: list[str]
    cost_usd: float


@router.get("", response_model=BrainOut)
def brain_graph(_ctx: CurrentAdmin, session: AdminDb, days: int | None = Query(default=None, ge=1, le=3650)) -> BrainOut:
    """Every agent, handoff, tool, opportunity, category, proposal, note, memory and directive, with their links.
    `days` keeps the proposals and events of that many days."""
    return BrainOut.model_validate(brain.build(session, days=days).as_dict())


@router.post("/ask", response_model=BrainAnswerOut)
def brain_ask_question(data: BrainAskIn, ctx: OwnerAdminBrief) -> BrainAnswerOut:
    # The answer takes a model call: short transactions, none open while it thinks.
    def db() -> Any:
        return admin_transaction(write=True)

    try:
        answer = brain_ask.ask(db, question=data.question, days=data.days)
    except brain_ask.Unavailable as exc:
        raise ValidationFailed(str(exc), code="BRAIN_NOT_READY") from exc
    with admin_transaction(write=True) as session:
        record(session, ctx.admin, "brain.asked", target_type="brain", ip=ctx.ip, metadata={"cited": answer.cited[:20]})
    return BrainAnswerOut(text=answer.text, cited=answer.cited, found=answer.found, cost_usd=round(answer.cost_usd, 6))
