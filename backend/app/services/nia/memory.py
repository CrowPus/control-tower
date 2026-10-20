"""Nia's memory: who the owner is, their decisions and
preferences, the work Nia and the owner did together, lessons with their evidence and facts — each with its topic (the
branch of the memory tree) and where it came from. The owner sees, corrects and deletes every one; Nia reads them
before it answers (chat, voice and the browser alike) and finds older ones by search.

The owner's profile (Admin → Nia → Memory → About you; platform setting `nia_owner`) says who Nia talks with and how
to address them; the owner writes it — Nia never guesses a name."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ...config import settings
from ...models import MEMORY_KINDS, NiaConversation, NiaMemory, NiaMessage
from .. import platform

# How many memories go into every answer; the rest are found by search.
IN_PROMPT = 80


def add(session: Session, *, kind: str, text: str, evidence: str = "", source: str = "", topic: str = "") -> NiaMemory:
    if kind not in MEMORY_KINDS:
        raise ValueError(f"A memory is one of: {', '.join(MEMORY_KINDS)}.")
    text = text.strip()
    if not text:
        raise ValueError("A memory needs its text.")
    now = datetime.now(UTC)
    same = session.execute(select(NiaMemory).where(NiaMemory.kind == kind, NiaMemory.text == text[:4000], NiaMemory.active.is_(True))).scalar_one_or_none()
    if same is not None:
        same.updated_at = now
        if topic and not same.topic:
            same.topic = clean_topic(topic)
        return same
    row = NiaMemory(kind=kind, topic=clean_topic(topic), text=text[:4000], evidence=evidence.strip()[:2000], source=source[:120], active=True, created_at=now, updated_at=now)
    session.add(row)
    session.flush()
    return row


# The order Nia reads them in: who the owner is and the rules they set first, then what was done lately.
ORDER = {"person": 0, "preference": 1, "decision": 2, "work": 3, "lesson": 4, "fact": 5}


def clean_topic(topic: str) -> str:
    return " ".join((topic or "").split())[:60].strip().lower()


def recent(session: Session, limit: int = IN_PROMPT) -> list[NiaMemory]:
    # Everything about the owner and every rule always; then the newest work, lessons and facts.
    rows = list(session.execute(select(NiaMemory).where(NiaMemory.active.is_(True)).order_by(NiaMemory.updated_at.desc()).limit(limit * 3)).scalars())
    rows.sort(key=lambda m: (ORDER.get(m.kind, 9) if m.kind in ("person", "preference", "decision") else 3, -m.updated_at.timestamp()))
    return rows[:limit]


def search(session: Session, query: str, limit: int = 20) -> list[NiaMemory]:
    words = [w for w in re.findall(r"\w{3,}", query.lower())][:8]
    if not words:
        return recent(session, limit)
    rows = list(session.execute(select(NiaMemory).where(NiaMemory.active.is_(True), or_(*[NiaMemory.text.ilike(f"%{w}%") for w in words]))).scalars())
    rows.sort(key=lambda m: -sum(w in m.text.lower() for w in words))
    return rows[:limit]


# --- who Nia talks with, and where we left off -----------------------------------------------------------------
OWNER_KEY = "nia_owner"
OWNER_FIELDS = ("name", "address_as", "role", "about")


def owner(session: Session) -> dict[str, str]:
    raw = platform.get(session, OWNER_KEY) or {}
    return {k: str(raw.get(k) or "").strip() for k in OWNER_FIELDS}


def save_owner(session: Session, values: dict[str, Any]) -> dict[str, str]:
    platform.put(session, OWNER_KEY, {k: str(values.get(k) or "").strip()[:10000 if k == "about" else 300] for k in OWNER_FIELDS})
    return owner(session)


def owner_block(session: Session) -> str:
    me = owner(session)
    if not me["name"]:
        return (f"WHO YOU ARE TALKING WITH: the owner of {settings().app_name}. You do not know their name yet: ask once, kindly, how they would like "
                "to be addressed, and suggest they write it in Nia → Memory → About you so you always know it.")
    lines = [f"WHO YOU ARE TALKING WITH: {me['name']}, the owner of {settings().app_name}."]
    lines.append(f"Address them as: {me['address_as'] or me['name']}.")
    if me["role"]:
        lines.append(f"Their role: {me['role']}.")
    if me["about"]:
        lines.append(f"What they told you about themselves: {me['about']}")
    return "\n".join(lines)


def memory_block(session: Session) -> str:
    rows = recent(session)
    if not rows:
        return "(nothing yet)"
    return "\n".join(f"- [{m.kind}{' · ' + m.topic if m.topic else ''}] {m.text}" + (f" (evidence: {m.evidence})" if m.evidence else "")
                     + (f" — {m.updated_at.date().isoformat()}" if m.kind == "work" else "") for m in rows)


LEFT_OFF = 14  # the last messages shown when a talk starts fresh


def left_off(session: Session, *, skip_conversation: int | None = None) -> str:
    """The end of the last conversation (typed or spoken), so a new talk picks up where the last one ended."""
    stmt = select(NiaConversation).order_by(NiaConversation.updated_at.desc())
    if skip_conversation is not None:
        stmt = stmt.where(NiaConversation.id != skip_conversation)
    last = session.execute(stmt.limit(1)).scalar_one_or_none()
    if last is None:
        return ""
    rows = list(session.execute(select(NiaMessage).where(NiaMessage.conversation_id == last.id).order_by(NiaMessage.id.desc()).limit(LEFT_OFF)).scalars())[::-1]
    if not rows:
        return ""
    return (f"WHERE YOU LEFT OFF (the end of your last conversation, {last.updated_at.date().isoformat()}; what you said you could not do "
            "then may be possible now — see WHAT YOU CAN DO NOW):\n") + "\n".join(
        f"{'OWNER' if m.role == 'owner' else 'NIA'}: {m.text[:600]}" for m in rows)


# What each tool lets Nia do, in her own words: she is told what she can do NOW, so an earlier "I cannot" never sticks.
ABILITIES = {
    "computer_files": "look into the owner's folders (Documents, Downloads, Desktop)",
    "computer_search": "search the owner's files",
    "computer_read": "read PDF, Word and text files and look at pictures on the owner's computer",
    "computer_show": "OPEN a PDF, picture or text file on the owner's computer so the owner sees it (in your Chrome window there)",
    "computer_window": "CLOSE your Chrome window on the owner's computer, look at what it shows, or go back a page",
    "computer_screen": "SEE the owner's whole screen (which windows and pictures are open) — only see, never click",
    "computer_write": "create files in your own folder on the owner's computer",
    "computer_change": "delete, move or rename files on the owner's computer (after reading it back and the owner's yes)",
    "browse": "work in a web browser the owner watches",
    "print_company_letter": "print a company's letter on the office printer",
    "print_letters": "print a batch of letters",
    "propose_admin_change": "change things in the admin (after the owner approves)",
}


def abilities(granted: tuple[str, ...] | list[str] | None) -> str:
    if not granted:
        return ""
    can = [text for name, text in ABILITIES.items() if name in granted]
    if not can:
        return ""
    return ("WHAT YOU CAN DO NOW (your tools — trust this over anything said in earlier conversations; your abilities grow, so an "
            "earlier \"I cannot\" may be out of date): " + "; ".join(can) + ". When the owner asks for one of these, use the tool — never "
            "say you cannot. If a tool answers with an error, say exactly what it said. NEVER say you did something (opened, closed, "
            "clicked, sent, saved) unless a tool did it in this conversation and answered without an error; if you have no tool for "
            "it, say so plainly and what you can do instead.")


def context(session: Session, *, skip_conversation: int | None = None, with_left_off: bool = True, granted: tuple[str, ...] | list[str] | None = None) -> str:
    """Everything Nia knows before she says a word: who the owner is, what she can do now, what she remembers, where they left off."""
    parts = [owner_block(session)]
    can = abilities(granted)
    if can:
        parts.append(can)
    parts.append("WHAT YOU REMEMBER (person, preference and decision are rules; work is what you did together):\n" + memory_block(session))
    if with_left_off:
        last = left_off(session, skip_conversation=skip_conversation)
        if last:
            parts.append(last)
    from . import truth

    parts.append(truth.block(session))
    parts.append(
        "MEMORY: you have a real memory — use it. Greet the owner by how they like to be addressed, and when a talk starts, pick up "
        "where you left off in one sentence (what you were doing, what is still open) instead of asking what you can do. Call remember "
        "(with a short topic) whenever the owner tells you something about themselves (person), decides something (decision), says how "
        "they like things (preference), or when you finish a piece of work together (work: what was done, the result, what is open). "
        "Never store a member's or a company person's personal data. If you do not remember something, search read_memories before "
        "saying you do not know.")
    return "\n\n".join(parts)
