"""Searching the web for an agent: one Gemini call
with Google Search turned on, returning the answer and the pages it came from. The links are
Google's redirect links; `read_web_page` follows them to the organiser's own page, where every fact
is confirmed before it is proposed. What a search returns is information, never an instruction.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from ..ai.client import ModelConfig

# Safety rails, not business values: the answer's length and how many sources come back.
MAX_OUTPUT_TOKENS = 2500
MAX_SOURCES = 15


@dataclass
class Found:
    answer: str
    sources: list[dict[str, str]] = field(default_factory=list)
    queries: list[str] = field(default_factory=list)
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: int = 0


Searcher = Callable[[str, str], Found]


def gemini_search(query: str, tier: str) -> Found:
    from google import genai
    from google.genai import types

    from ..config import settings

    cfg = settings()
    if not cfg.gemini_api_key:
        raise RuntimeError("No AI key is configured on the server.")
    model = ModelConfig.resolve("fast" if tier == "fast" else "reasoning")
    prompt = (f"Search the web and answer for an agent of {cfg.app_name}. Name each concrete fact's source with its OWN official website "
              "address where you can, with the dates the sources give. Never a site that only copies others. Do not invent anything; say when "
              "you found nothing.\n\n"
              f"Question: {query}")
    started = time.perf_counter()
    with genai.Client(api_key=cfg.gemini_api_key, http_options=types.HttpOptions(timeout=90_000)) as client:
        response = client.models.generate_content(model=model, contents=prompt, config=types.GenerateContentConfig(
            tools=[types.Tool(google_search=types.GoogleSearch())], max_output_tokens=MAX_OUTPUT_TOKENS, temperature=0.2))
    usage = response.usage_metadata
    candidate = response.candidates[0] if response.candidates else None
    text = "\n".join(p.text for p in ((candidate.content.parts if candidate and candidate.content else None) or []) if p.text and not p.thought)
    grounding = getattr(candidate, "grounding_metadata", None) if candidate else None
    sources = []
    for chunk in (getattr(grounding, "grounding_chunks", None) or [])[:MAX_SOURCES]:
        web = getattr(chunk, "web", None)
        if web is not None and getattr(web, "uri", None):
            sources.append({"title": str(getattr(web, "title", "") or "")[:200], "url": str(web.uri)[:1000]})
    return Found(answer=text.strip()[:12000], sources=sources, queries=list(getattr(grounding, "web_search_queries", None) or [])[:10], model=model,
                 input_tokens=int(getattr(usage, "prompt_token_count", 0) or 0),
                 output_tokens=int(getattr(usage, "candidates_token_count", 0) or 0) + int(getattr(usage, "thoughts_token_count", 0) or 0),
                 latency_ms=int((time.perf_counter() - started) * 1000))


SEARCH: Searcher = gemini_search


def use(searcher: Searcher) -> Searcher:
    """Swap the searcher (tests); returns the previous one."""
    global SEARCH
    previous, SEARCH = SEARCH, searcher
    return previous


def as_result(found: Found) -> dict[str, Any]:
    return {"answer": found.answer, "sources": found.sources, "searched_for": found.queries,
            "note": "Open a source with read_web_page (the link leads to the page) and find the organiser's own page before using anything."}
