"""The admin's own documentation (Admin → Docs, the "?" on every page, and what Nia reads).

One Markdown file per area in this folder. Each starts with a few header lines, then a line
with three dashes, then the text:

    key: letters
    title: Letters
    order: 60
    screens: /letters, /letters/batches/:id
    api: /admin-api/letters
    summary: One sentence for the list and search.
    ---
    ## What it is for
    ...

`screens` are the admin pages the section explains (the "?" opens the longest match);
`api` are the server paths behind them (tests/test_9_admin_docs.py checks that every admin
page and every admin server function belongs to a section, so the docs cannot fall behind).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path

FOLDER = Path(__file__).parent


@dataclass(frozen=True)
class Section:
    key: str
    title: str
    order: int
    summary: str
    body: str
    screens: tuple[str, ...] = field(default=())
    api: tuple[str, ...] = field(default=())


def _parse(path: Path) -> Section:
    text = path.read_text(encoding="utf-8")
    head, sep, body = text.partition("\n---\n")
    if not sep:
        raise ValueError(f"{path.name}: header and text must be separated by a line with ---")
    meta: dict[str, str] = {}
    for line in head.splitlines():
        if line.strip():
            name, _, value = line.partition(":")
            meta[name.strip()] = value.strip()

    def items(key: str) -> tuple[str, ...]:
        return tuple(x.strip() for x in meta.get(key, "").split(",") if x.strip())

    return Section(key=meta["key"], title=meta["title"], order=int(meta.get("order", "999")), summary=meta.get("summary", ""), body=body.strip(),
                   screens=items("screens"), api=items("api"))


@cache
def sections() -> tuple[Section, ...]:
    found = [_parse(p) for p in sorted(FOLDER.glob("*.md"))]
    return tuple(sorted(found, key=lambda s: (s.order, s.title)))


def get(key: str) -> Section | None:
    return next((s for s in sections() if s.key == key), None)


def _pattern(screen: str) -> re.Pattern[str]:
    return re.compile("^" + re.sub(r":[a-z_]+", "[^/]+", screen.rstrip("/*")) + "(/.*)?$")


def for_screen(path: str) -> Section | None:
    """The section that explains an admin page (the most specific screen wins)."""
    best: tuple[int, Section] | None = None
    for section in sections():
        for screen in section.screens:
            if _pattern(screen).match(path.rstrip("/") or "/") and (best is None or len(screen) > best[0]):
                best = (len(screen), section)
    return best[1] if best else None


def search(query: str, limit: int = 8) -> list[tuple[Section, str]]:
    """Sections that mention every word of the query, best first, with a line that matches."""
    words = [w for w in re.findall(r"\w+", query.lower()) if len(w) > 1]
    if not words:
        return []
    scored = []
    for section in sections():
        haystack = f"{section.title}\n{section.summary}\n{section.body}".lower()
        if all(w in haystack for w in words):
            score = sum(haystack.count(w) for w in words) + sum(10 for w in words if w in section.title.lower())
            line = next((ln.strip(" #-*") for ln in section.body.splitlines() if any(w in ln.lower() for w in words) and ln.strip()), section.summary)
            scored.append((score, section, line[:240]))
    scored.sort(key=lambda x: -x[0])
    return [(s, line) for _score, s, line in scored[:limit]]
