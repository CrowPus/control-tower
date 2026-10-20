"""Admin → Docs (backend/app/admin_docs): every admin page and every admin server function
belongs to a documented section, links inside the docs lead somewhere, and admins read them
through the API. A new page or function without documentation fails here."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from app import admin_docs
from app.main import app as admin_app
from tests.test_9_team import admin  # noqa: F401 - the owner signed in to the admin app (fixture)

APP_TSX = Path(__file__).resolve().parents[2] / "admin" / "src" / "App.tsx"
# Not admin pages: the invitation link opened before signing in, and the start address (it
# opens Insights).
NOT_PAGES = {"/accept-invite", "/"}


def _screens() -> list[str]:
    paths = re.findall(r'<Route path="([^"]+)"', APP_TSX.read_text(encoding="utf-8"))
    return sorted({("/" + p.lstrip("/")).removesuffix("/*") or "/" for p in paths} - NOT_PAGES)


def _api() -> list[str]:
    # The admin's API description lists every function (the app's own route list nests routers).
    paths = sorted(p for p in admin_app.openapi()["paths"] if p.startswith("/admin-api"))
    assert len(paths) > 100, "every admin function, not only a few"
    return paths


def test_every_section_is_well_formed() -> None:
    sections = admin_docs.sections()
    keys = [s.key for s in sections]
    assert len(keys) == len(set(keys)), "each section has its own key"
    for s in sections:
        assert s.title and s.summary and len(s.body) > 200, s.key
        assert "## " in s.body, (s.key, "headings make a section readable")


def test_every_admin_page_is_documented() -> None:
    missing = [p for p in _screens() if admin_docs.for_screen(re.sub(r":[a-z_]+", "1", p)) is None]
    assert not missing, f"admin pages without a docs section: {missing}"


def test_every_admin_function_is_documented() -> None:
    prefixes = [a for s in admin_docs.sections() for a in s.api]
    missing = [p for p in _api() if not any(p == a or p.startswith(a.rstrip("/") + "/") or p.startswith(a + "?") for a in prefixes)]
    assert not missing, f"admin server functions without a docs section: {missing}"


def test_links_in_the_docs_lead_somewhere() -> None:
    keys = {s.key for s in admin_docs.sections()}
    screens = _screens()
    patterns = [re.compile("^" + re.sub(r":[a-z_]+", "[^/]+", p) + "$") for p in screens]
    for s in admin_docs.sections():
        for target in re.findall(r"\]\(([^)]+)\)", s.body):
            if target.startswith("docs:"):
                assert target[5:] in keys, (s.key, target)
            elif target.startswith("/"):
                assert any(p.match(target.split("?")[0]) for p in patterns), (s.key, target)


def test_the_most_specific_page_wins_and_search_finds_words() -> None:
    assert admin_docs.for_screen("/docs").key == "docs"  # type: ignore[union-attr]
    team = admin_docs.for_screen("/team/qa_runner")
    assert team is not None and team.key == "team"
    assert admin_docs.search("") == []
    found = admin_docs.search("approval")
    assert found and all("approv" in (s.body + s.title).lower() for s, _ in found)


def test_admins_read_the_docs_through_the_api(admin: Any) -> None:  # noqa: F811 - the fixture
    listed = admin.get("/admin-api/docs").json()
    assert [d["key"] for d in listed] == [s.key for s in admin_docs.sections()]
    one = admin.get(f"/admin-api/docs/{listed[0]['key']}").json()
    assert one["body"] and one["title"] == listed[0]["title"]
    assert admin.get("/admin-api/docs/for-screen", params={"path": "/team/nia"}).json()["key"] == "team"
    assert admin.get("/admin-api/docs/no-such-section").status_code == 404
    assert admin.get("/admin-api/docs", params={"q": "approve"}).json()


def test_nia_reads_the_same_docs_with_her_tool() -> None:
    from app.services.team import registry, tools

    assert "read_admin_docs" in registry.tools_of(registry.AGENTS["nia"])
    read = tools.CATALOGUE["read_admin_docs"][0]
    listed = read(None)  # type: ignore[arg-type]
    assert [s["section"] for s in listed["sections"]] == [s.key for s in admin_docs.sections()]
    page = read(None, page="/insights/agents")  # type: ignore[arg-type]
    assert page["key"] == "insights" and "Agents" in page["text"]
    assert read(None, section="approvals")["title"] == "Clearance"  # type: ignore[arg-type]
    assert read(None, query="clearance")["results"]  # type: ignore[arg-type]
    assert "error" in read(None, section="nothing-like-this")  # type: ignore[arg-type]



def test_every_admin_page_answers_without_a_server_error(admin: Any) -> None:  # noqa: F811
    """Each admin read without parameters answers (a field left over from an earlier product once broke /admin-api/trust)."""
    from app.main import app as admin_app

    paths = sorted({p for p, ops in admin_app.openapi().get("paths", {}).items() if "get" in ops and "{" not in p and p.startswith("/admin-api/")})
    failed = [(p, r.status_code) for p in paths if (r := admin.get(p)).status_code >= 500]
    assert not failed, failed
