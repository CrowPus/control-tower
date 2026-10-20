"""The company's character: one figure the owner created, who appears in the company's
videos so that people come to know it and know it is ours. The owner uploads its pictures and describes it
(Admin → Marketing → Character); the Graphic Designer and the video studio use exactly this — the person
stays the same, while clothes, place and situation may change from video to video."""

from __future__ import annotations

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...models import MarketingMedia
from .. import platform

KEY = "character"
# The video model takes at most three pictures of a character.
MAX_PICTURES = 3


class Character(BaseModel):
    name: str = Field(default="", max_length=60)
    # Who the character is, in a few sentences (role, age range, what they stand for).
    who: str = Field(default="", max_length=2000)
    # What never changes: face, hair, skin, build, age, typical features — what makes it recognisable.
    look: str = Field(default="", max_length=2000)
    personality: str = Field(default="", max_length=2000)
    # Whether it speaks at all. The owner's choice (9 Oct 2026): no — a silent character, its comedy told by action, facial
    # expression, music and sound effects (the video model would give it a different voice in each scene).
    speaks: bool = False
    # How it sounds when it speaks (the video model is told this only when it speaks).
    voice: str = Field(default="", max_length=600)
    # The owner's rules: what the character always or never does, says or wears.
    rules: str = Field(default="", max_length=2000)
    # Its pictures in the media library (uploaded by the owner), the best one first.
    picture_ids: list[int] = Field(default_factory=list, max_length=MAX_PICTURES)

    @property
    def ready(self) -> bool:
        return bool(self.name.strip() and self.picture_ids)


def get(session: Session) -> Character:
    return Character.model_validate(platform.get(session, KEY) or {})


def save(session: Session, value: Character) -> Character:
    ids = list(dict.fromkeys(int(i) for i in value.picture_ids))[:MAX_PICTURES]
    if ids:
        found = {m.id for m in session.execute(select(MarketingMedia).where(MarketingMedia.id.in_(ids), MarketingMedia.kind == "image")).scalars()}
        missing = [i for i in ids if i not in found]
        if missing:
            raise ValueError("The character's pictures are pictures in the media library.")
    value = value.model_copy(update={"picture_ids": ids})
    platform.put(session, KEY, value.model_dump())
    return value


def pictures(session: Session, value: Character | None = None) -> list[MarketingMedia]:
    """The character's pictures in its order (those still in the library)."""
    value = value or get(session)
    if not value.picture_ids:
        return []
    rows = {m.id: m for m in session.execute(select(MarketingMedia).where(MarketingMedia.id.in_(value.picture_ids))).scalars()}
    return [rows[i] for i in value.picture_ids if i in rows and rows[i].kind == "image" and rows[i].data]


def describe(value: Character) -> str:
    """The character as the models are told about it."""
    parts = [f"The company's character {value.name}" + (f": {value.who}" if value.who else "")]
    if value.look:
        parts.append(f"Looks (never changes): {value.look}")
    if value.personality:
        parts.append(f"Personality: {value.personality}")
    if value.speaks and value.voice:
        parts.append(f"Voice: {value.voice}")
    if not value.speaks:
        parts.append(f"{value.name or 'The character'} never speaks: a silent character, told by action, facial expression, music and sound effects.")
    if value.rules:
        parts.append(f"Rules: {value.rules}")
    parts.append("Clothes, place and situation may change; the person (face, hair, skin, build, age) is always exactly the one in the reference pictures.")
    return "\n".join(parts)
