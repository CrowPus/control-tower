"""Admin → Docs: the admin's own documentation (app/admin_docs), for every admin."""

from __future__ import annotations

from fastapi import APIRouter, Query
from pydantic import BaseModel

from .. import admin_docs
from ..core.errors import NotFound
from .deps import CurrentAdmin

router = APIRouter(prefix="/admin-api/docs", tags=["admin-docs"])


class DocSummaryOut(BaseModel):
    key: str
    title: str
    summary: str
    screens: list[str]
    # With a search: the line that matched.
    match: str = ""


class DocOut(DocSummaryOut):
    body: str


def _summary(section: admin_docs.Section, match: str = "") -> DocSummaryOut:
    return DocSummaryOut(key=section.key, title=section.title, summary=section.summary, screens=list(section.screens), match=match)


@router.get("", response_model=list[DocSummaryOut])
def docs(_ctx: CurrentAdmin, q: str = Query(default="", max_length=200)) -> list[DocSummaryOut]:
    if q.strip():
        return [_summary(s, line) for s, line in admin_docs.search(q, limit=20)]
    return [_summary(s) for s in admin_docs.sections()]


@router.get("/for-screen", response_model=DocOut)
def doc_for_screen(_ctx: CurrentAdmin, path: str = Query(max_length=300)) -> DocOut:
    section = admin_docs.for_screen(path)
    if section is None:
        raise NotFound("No documentation for this page yet.", code="DOC_NOT_FOUND")
    return DocOut(**_summary(section).model_dump(), body=section.body)


@router.get("/{key}", response_model=DocOut)
def doc(key: str, _ctx: CurrentAdmin) -> DocOut:
    section = admin_docs.get(key)
    if section is None:
        raise NotFound("No such page in the docs.", code="DOC_NOT_FOUND")
    return DocOut(**_summary(section).model_dump(), body=section.body)
