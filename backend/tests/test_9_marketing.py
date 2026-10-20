"""Marketing: the brand kit and the picture studio within the owner's budget,
the Marketing Lead's plan and its briefs, social posts checked when proposed and published after
approval on the connected networks (Meta, LinkedIn, YouTube — answered here by a fake of their APIs),
video posts waiting for the owner's video, posts by hand, results, and connecting a network."""

from __future__ import annotations

import io
import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from PIL import Image
from sqlalchemy import delete, select

from app.config import settings
from app.core import clock, crypto
from app.db import transaction
from app.models import (
    AIUsage,
    MarketingMedia,
    MarketingPlan,
    SocialAccount,
    SocialPost,
    TeamApproval,
    TeamEvent,
)
from app.services import platform
from app.services.marketing import brand, plans, studio
from app.services.social import accounts, meta, net, posts, video
from app.services.team import approvals, events, registry
from tests.test_9_team import admin  # noqa: F401 - the owner signed in to the admin app (fixture)


def _db() -> Any:
    return transaction(write=True)


def _png(width: int = 800, height: int = 1000, color: tuple[int, int, int] = (40, 120, 200)) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (width, height), color).save(out, format="PNG")
    return out.getvalue()


class FakeNetworks:
    """Meta, LinkedIn, Google and a video host, as far as Acme talks to them."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, Any]]] = []
        self.ig_status = "FINISHED"

    def __call__(self, request: httpx.Request) -> httpx.Response:
        url = urlparse(str(request.url))
        body: dict[str, Any] = {}
        if request.content and request.headers.get("content-type", "").startswith("application/x-www-form-urlencoded"):
            body = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
        elif request.content and request.headers.get("content-type", "").startswith("application/json"):
            body = json.loads(request.content)
        self.calls.append((request.method, f"{url.netloc}{url.path}", body or dict(parse_qs(url.query))))
        host, path = url.netloc, url.path
        if host == "graph.facebook.com":
            if path.endswith("/oauth/access_token"):
                return httpx.Response(200, json={"access_token": "user-token"})
            if path.endswith("/me/accounts"):
                return httpx.Response(200, json={"data": [{"id": "111", "name": "Acme", "access_token": "page-token", "followers_count": 120,
                                                           "instagram_business_account": {"id": "999", "username": "the website", "followers_count": 340}}]})
            if path.endswith("/111/photos"):
                return httpx.Response(200, json={"id": "p1", "post_id": "111_555"})
            if path.endswith("/111/feed"):
                return httpx.Response(200, json={"id": "111_556"})
            if path.endswith("/111/videos"):
                return httpx.Response(200, json={"id": "v77"})
            if path.endswith("/999/media"):
                return httpx.Response(200, json={"id": "container-1"})
            if path.endswith("/container-1"):
                return httpx.Response(200, json={"status_code": self.ig_status, "status": "Error: unsupported format" if self.ig_status == "ERROR" else ""})
            if path.endswith("/999/media_publish"):
                return httpx.Response(200, json={"id": "ig-media-1"})
            if path.endswith("/ig-media-1"):
                return httpx.Response(200, json={"permalink": "https://www.instagram.com/p/ABC/", "like_count": 12, "comments_count": 3})
            if path.endswith("/ig-media-1/insights"):
                metric = parse_qs(url.query)["metric"][0]
                return httpx.Response(200, json={"data": [{"name": metric, "values": [{"value": {"reach": 500, "views": 900, "saved": 7, "shares": 2}[metric]}]}]})
            if path.endswith("/999"):
                return httpx.Response(200, json={"followers_count": 360})
            if path.endswith("/111"):
                return httpx.Response(200, json={"followers_count": 125})
            return httpx.Response(404, json={"error": {"message": f"unknown {path}"}})
        if host == "www.linkedin.com" and path.endswith("/accessToken"):
            return httpx.Response(200, json={"access_token": "li-token", "expires_in": 5184000, "refresh_token": "li-refresh"})
        if host == "api.linkedin.com":
            if path.endswith("/organizationAcls"):
                return httpx.Response(200, json={"elements": [{"organization": "urn:li:organization:4242"}]})
            if path.endswith("/organizations/4242"):
                return httpx.Response(200, json={"localizedName": "Acme", "vanityName": "acme"})
            if "networkSizes" in path:
                return httpx.Response(200, json={"firstDegreeSize": 77})
            if path.endswith("/images"):
                return httpx.Response(200, json={"value": {"uploadUrl": "https://upload.linkedin.example/img1", "image": "urn:li:image:I1"}})
            if path.endswith("/videos") and "initializeUpload" in url.query:
                size = body["initializeUploadRequest"]["fileSizeBytes"]
                return httpx.Response(200, json={"value": {"video": "urn:li:video:V1", "uploadToken": "t", "uploadInstructions": [
                    {"uploadUrl": "https://upload.linkedin.example/v1", "firstByte": 0, "lastByte": size - 1}]}})
            if path.endswith("/videos") and "finalizeUpload" in url.query:
                return httpx.Response(200)
            if path.endswith("/posts"):
                return httpx.Response(201, headers={"x-restli-id": "urn:li:share:7001"})
            return httpx.Response(404, json={"message": f"unknown {path}"})
        if host == "upload.linkedin.example":
            return httpx.Response(201, headers={"etag": '"etag-1"'})
        if host == "oauth2.googleapis.com":
            return httpx.Response(200, json={"access_token": "yt-token", "expires_in": 3600, "refresh_token": "yt-refresh"})
        if host == "www.googleapis.com":
            if path.endswith("/channels"):
                return httpx.Response(200, json={"items": [{"id": "UC1", "snippet": {"title": "Acme", "customUrl": "@acme"}, "statistics": {"subscriberCount": "45"}}]})
            if path.startswith("/upload/youtube"):
                return httpx.Response(200, headers={"location": "https://www.googleapis.com/upload/session/abc"})
            if path.startswith("/upload/session"):
                return httpx.Response(200, json={"id": "yt-vid-1"})
        if host == "videos.example":
            return httpx.Response(200, content=b"\x00" * 50_000, headers={"content-type": "video/mp4"})
        return httpx.Response(404, json={"error": f"unknown {host}{path}"})


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeNetworks]:
    networks = FakeNetworks()
    monkeypatch.setattr(net, "transport", httpx.MockTransport(networks))
    monkeypatch.setattr(meta, "sleep_seconds", 0)
    monkeypatch.setattr(settings(), "public_base_url", "https://app.tower.example")
    monkeypatch.setattr(settings(), "admin_public_base_url", "https://admin.tower.example")
    monkeypatch.setattr(studio, "generator", lambda prompt, aspect, model: studio.Made(data=_png(), mime_type="image/png", model=model))
    yield networks


@pytest.fixture(autouse=True)
def clean() -> Iterator[None]:
    yield
    with _db() as db:
        for model in (SocialPost, MarketingPlan, SocialAccount, MarketingMedia, TeamEvent, TeamApproval):
            db.execute(delete(model))
        db.execute(delete(AIUsage).where(AIUsage.operation == studio.OPERATION))
        for key in ("brand", "marketing", "social", "social_apps", accounts.STATE_KEY):
            platform.put(db, key, {})


def _connect_meta(db: Any) -> tuple[SocialAccount, SocialAccount]:
    now = datetime.now(UTC)
    page = SocialAccount(network="facebook", external_id="111", name="Acme", created_at=now, updated_at=now)
    db.add(page)
    db.flush()
    page.token_enc = crypto.encrypt("page-token", associated_data="social_account:facebook:111:token")
    ig = SocialAccount(network="instagram", external_id="999", name="the website", parent_id=page.id, created_at=now, updated_at=now)
    db.add(ig)
    db.flush()
    return page, ig


# --- the studio --------------------------------------------------------------------------------------------
def test_pictures_are_made_only_within_the_owners_budget_and_carry_the_brand_and_the_ai_label(fake: FakeNetworks) -> None:
    with _db() as db:
        with pytest.raises(studio.StudioRefused, match="monthly picture budget"):
            studio.make(db, prompt="A forklift driver in a bright warehouse in Kulmbach", created_by="content_community")
        brand.save_settings(db, brand.MarketingSettings(monthly_picture_budget_usd=0.1))
        logo = studio.upload(db, data=_png(300, 100, (255, 255, 255)), filename="logo.png", title="Logo")
        brand.save_kit(db, brand.BrandKit(logo_media_id=logo.id, primary_color="#0F6B5C"))
        made = studio.make(db, prompt="A forklift driver in a bright warehouse in Kulmbach", headline="Staplerfahrer gesucht", subline="Kulmbach · Vollzeit",
                           shows_people=True, created_by="content_community")
        assert made.mime_type == "image/jpeg" and made.ai_generated and made.shows_people and made.cost_usd == settings().ai_image_price_usd
        picture = Image.open(io.BytesIO(made.data or b""))
        assert picture.size == (800, 1000)
        assert picture.getpixel((400, 990)) != (40, 120, 200), "the headline band in the brand's colour"
        assert db.execute(select(AIUsage).where(AIUsage.operation == studio.OPERATION)).scalar_one().estimated_cost_usd == made.cost_usd
        with pytest.raises(studio.StudioRefused, match="used up"):
            studio.make(db, prompt="A second picture of a nurse in a care home", created_by="content_community")
        with pytest.raises(studio.StudioRefused, match="format"):
            studio.make(db, prompt="A truck at sunrise on a German motorway", aspect="7:3")
    assert video.direct_link("https://drive.google.com/file/d/ABC_123/view?usp=sharing") == "https://drive.google.com/uc?export=download&confirm=t&id=ABC_123"
    assert video.direct_link("https://www.dropbox.com/s/x/clip.mp4?dl=0").endswith("dl=1") and video.direct_link("http://x/clip.mp4") == ""
    assert "No brand names, logos or trademarks" in studio.RULES and "Control Tower" in studio.house_rules(brand.BrandKit(name="Control Tower"))


def test_an_agent_makes_a_picture_with_its_tool() -> None:
    from app.services.team import tools

    with _db() as db:
        brand.save_settings(db, brand.MarketingSettings(monthly_picture_budget_usd=5))
    ctx = tools.Context(agent=registry.AGENTS["content_community"], run_id=0, db=_db, granted=("create_image",))
    original = studio.generator
    studio.generator = lambda prompt, aspect, model: studio.Made(data=_png(), mime_type="image/png", model=model)
    try:
        out = tools._create_image(ctx, prompt="Two apprentices in a bakery in Bamberg laughing", aspect="1:1", shows_people=True)
    finally:
        studio.generator = original
    assert out["media_id"] and out["budget"]["spent_this_month_usd"] > 0
    assert "brand" in tools._brand_kit(ctx) and tools._media_library(ctx)["media"][0]["id"] == out["media_id"]


# --- the plan and its briefs -----------------------------------------------------------------------------
def test_the_marketing_lead_plans_and_each_specialist_gets_only_its_brief() -> None:
    lead = registry.AGENTS["marketing_lead"]
    assert lead.department == "Marketing" and "marketing.plan" in registry.actions_of(lead) and registry.ACTIONS["marketing.plan"].ceiling == "L3"
    assert registry.AGENTS["content_community"].name == "Social Media Manager" and "create_image" in registry.AGENTS["content_community"].tools
    payload = {"title": "Fahrer für Kulmbach", "period_start": "2026-10-07", "period_end": "2026-10-20", "summary": "…",
               "goals": [{"metric": "new candidates for drivers in Kulmbach", "target": 40, "baseline": 5}],
               "campaigns": [{"name": "Fahrer", "channels": ["instagram", "facebook"]}],
               "briefs": {"content_community": "3 posts a week for drivers", "graphic_designer": "a scholarship series"}}
    with _db() as db:
        first = approvals.propose(db, agent="marketing_lead", action_type="marketing.plan", title="Plan 1", payload=payload)
        second = approvals.propose(db, agent="marketing_lead", action_type="marketing.plan", title="Plan 2", payload=payload)
        assert first.status == "withdrawn" and second.status == "pending", "a newer plan replaces the waiting one"
        approvals.decide(db, [second], "approve", admin_id=1)
        approvals.execute_due(db)
        plan = plans.active(db)
        assert plan is not None and plan.title == "Fahrer für Kulmbach" and plan.body["briefs"]["content_community"].startswith("3 posts")
        briefs = list(db.execute(select(TeamEvent).where(TeamEvent.type == "marketing.brief")).scalars())
        assert sorted(e.payload["to"] for e in briefs) == ["content_community", "graphic_designer"]
        social_events, _ = events.unseen(db, "content_community", 10)
        outreach_events, _ = events.unseen(db, "trust_compliance", 10)
        assert [e.payload["to"] for e in social_events if e.type == "marketing.brief"] == ["content_community"]
        assert not [e for e in outreach_events if e.type == "marketing.brief"], "a brief reaches only its agent"
        bad = approvals.propose(db, agent="marketing_lead", action_type="marketing.plan", title="Bad", payload={**payload, "briefs": {"nia": "x"}})
        approvals.decide(db, [bad], "approve", admin_id=1)
        approvals.execute_due(db)
        assert bad.status == "failed"


# --- posts --------------------------------------------------------------------------------------------------
def test_a_post_is_checked_when_proposed_and_goes_by_hand_where_nothing_is_connected(fake: FakeNetworks) -> None:
    with _db() as db:
        refused = pytest.raises(approvals.ProposalRefused, match="not connected")
        with refused:
            approvals.propose(db, agent="content_community", action_type="social.post", title="x", payload={"networks": ["instagram"], "kind": "image", "text": "Hallo", "media_ids": [1]})
        platform.put(db, "social", {"networks": ["x", "linkedin"]})
        with pytest.raises(approvals.ProposalRefused, match="Too long for x"):
            approvals.propose(db, agent="content_community", action_type="social.post", title="x", payload={"networks": ["x"], "text": "a" * 281})
        with pytest.raises(approvals.ProposalRefused, match="does not take video"):
            approvals.propose(db, agent="content_community", action_type="social.post", title="x", payload={"networks": ["x"], "kind": "video", "text": "a", "video_script": {"hook": "x"}})
        one = approvals.propose(db, agent="content_community", action_type="social.post", title="One", payload={"networks": ["linkedin"], "text": "Acme ist da."})
        two = approvals.propose(db, agent="content_community", action_type="social.post", title="Two", payload={"networks": ["x", "linkedin"], "text": "Noch einer.",
                                                                                                                 "texts": {"linkedin": "Für Unternehmen: noch einer."}})
        assert one.status == two.status == "pending", "several posts may wait at once"
        approvals.decide(db, [two], "approve", admin_id=1)
        approvals.execute_due(db)
        rows = {r.network: r for r in db.execute(select(SocialPost).where(SocialPost.approval_id == two.id)).scalars()}
        assert {n: r.status for n, r in rows.items()} == {"x": "ready", "linkedin": "ready"}, "not connected: posted by hand"
        assert rows["linkedin"].text == "Für Unternehmen: noch einer." and rows["x"].text == "Noch einer."


def test_an_approved_picture_post_goes_out_on_facebook_and_instagram_at_its_time_and_reads_its_results(fake: FakeNetworks) -> None:
    with _db() as db:
        page, ig = _connect_meta(db)
        brand.save_settings(db, brand.MarketingSettings(monthly_picture_budget_usd=5, posting_hours=[9, 18]))
        made = studio.make(db, prompt="A nurse in a modern care home in Bayreuth, warm light", shows_people=True, created_by="content_community")
        proposal = approvals.propose(db, agent="content_community", action_type="social.post", title="Pflege",
                                     payload={"networks": ["facebook", "instagram"], "kind": "image", "text": "Pflegekraft in Bayreuth gesucht? 💚", "media_ids": [made.id],
                                              "link": "https://app.tower.example/register?src=ig-pflege"})
        approvals.decide(db, [proposal], "approve", admin_id=1)
        approvals.execute_due(db)
        rows = {r.network: r for r in db.execute(select(SocialPost).where(SocialPost.approval_id == proposal.id)).scalars()}
        assert {r.status for r in rows.values()} == {"scheduled"}
        slot = rows["instagram"].scheduled_at
        assert slot is not None and slot.astimezone(clock.zone()).hour in (9, 18), "the owner's next posting hour"
        for row in rows.values():
            assert posts.publish(db, row)["posted"] == row.id, row.error
    fb_call = next(c for c in fake.calls if c[1].endswith("/111/photos"))
    assert fb_call[2]["url"].startswith("https://app.tower.example/api/v1/media/") and "AI-generated" in fb_call[2]["message"], "AI people are said to be so"
    assert "src=ig-pflege" in fb_call[2]["message"]
    with _db() as db:
        posted = {r.network: r for r in db.execute(select(SocialPost)).scalars()}
        assert posted["instagram"].post_url == "https://www.instagram.com/p/ABC/" and posted["facebook"].external_id == "111_555"
        assert posts.collect_metrics(db)["posts"] == 2
        ig_row = db.execute(select(SocialPost).where(SocialPost.network == "instagram")).scalar_one()
        assert ig_row.metrics["reach"] == 500 and ig_row.metrics["likes"] == 12
        assert db.execute(select(SocialAccount).where(SocialAccount.network == "instagram")).scalar_one().followers == 360
        summary = posts.results(db, 30)
        assert summary["by_network"]["instagram"]["reach"] == 500 and summary["best_posts"]


def test_instagram_failures_are_shown_on_the_post(fake: FakeNetworks) -> None:
    fake.ig_status = "ERROR"
    with _db() as db:
        _connect_meta(db)
        brand.save_settings(db, brand.MarketingSettings(monthly_picture_budget_usd=5))
        made = studio.make(db, prompt="A bricklayer on a building site in Coburg at noon")
        proposal = approvals.propose(db, agent="content_community", action_type="social.post", title="Bau", payload={"networks": ["instagram"], "kind": "image", "text": "Maurer!", "media_ids": [made.id]})
        approvals.decide(db, [proposal], "approve", admin_id=1)
        approvals.execute_due(db)
        row = db.execute(select(SocialPost)).scalar_one()
        assert posts.publish(db, row).get("failed") and row.status == "failed" and "unsupported format" in row.error


def test_a_video_post_waits_for_the_owners_video_and_then_goes_to_youtube_and_linkedin(fake: FakeNetworks, admin: Any) -> None:  # noqa: F811 - the fixture
    script = {"hook": "Du fährst gern?", "length_seconds": 30, "scenes": [{"seconds": "0-3", "shot": "LKW-Fahrerhaus", "on_screen_text": "Fahrer gesucht", "voiceover": "…"}]}
    now = datetime.now(UTC)
    with _db() as db:
        yt = SocialAccount(network="youtube", external_id="UC1", name="Acme", token_expires_at=now + timedelta(hours=1), created_at=now, updated_at=now)
        li = SocialAccount(network="linkedin", external_id="4242", name="Acme", token_expires_at=now + timedelta(days=30), created_at=now, updated_at=now)
        db.add_all([yt, li])
        db.flush()
        yt.token_enc = crypto.encrypt("yt-token", associated_data="social_account:youtube:UC1:token")
        li.token_enc = crypto.encrypt("li-token", associated_data="social_account:linkedin:4242:token")
        proposal = approvals.propose(db, agent="content_community", action_type="social.post", title="Fahrer-Video",
                                     payload={"networks": ["youtube", "linkedin"], "kind": "video", "text": "Fahrer (m/w/d) in Kulmbach (Vollzeit) #Logistik", "title": "Fahrer gesucht",
                                              "video_script": script})
        approvals.decide(db, [proposal], "approve", admin_id=1)
        approvals.execute_due(db)
        ids = {r.network: r.id for r in db.execute(select(SocialPost)).scalars()}
        assert {r.status for r in db.execute(select(SocialPost)).scalars()} == {"needs_video"}
    listed = admin.get("/admin-api/marketing/posts").json()
    assert listed[0]["video_script"]["hook"] == "Du fährst gern?"
    for network, post_id in ids.items():
        out = admin.post(f"/admin-api/marketing/posts/{post_id}/video", json={"link": "https://videos.example/clip.mp4"}).json()
        assert out["status"] == "scheduled", (network, out)
    with _db() as db:
        for row in db.execute(select(SocialPost)).scalars().all():
            assert posts.publish(db, row).get("posted"), row.error
        posted = {r.network: r for r in db.execute(select(SocialPost)).scalars()}
        assert posted["youtube"].post_url == "https://www.youtube.com/shorts/yt-vid-1" and posted["linkedin"].external_id == "urn:li:share:7001"
    start = next(c for c in fake.calls if c[1].startswith("www.googleapis.com/upload/youtube"))
    assert start[2]["snippet"]["title"] == "Fahrer gesucht" and "#Shorts" in start[2]["snippet"]["description"] and start[2]["status"]["privacyStatus"] == "public"
    assert not list(video.folder().iterdir()), "the owner's video is not kept"


def test_linkedin_posts_escape_its_reserved_characters_and_carry_their_pictures(fake: FakeNetworks) -> None:
    from app.services.social import linkedin

    assert linkedin.little_text("Fahrer (m/w/d) #Logistik @Acme") == "Fahrer \\(m/w/d\\) \\#Logistik \\@Acme"
    now = datetime.now(UTC)
    with _db() as db:
        li = SocialAccount(network="linkedin", external_id="4242", name="Acme", token_expires_at=now + timedelta(days=30), created_at=now, updated_at=now)
        db.add(li)
        db.flush()
        li.token_enc = crypto.encrypt("li-token", associated_data="social_account:linkedin:4242:token")
        brand.save_settings(db, brand.MarketingSettings(monthly_picture_budget_usd=5))
        made = studio.make(db, prompt="An HR manager reviewing applications on a laptop in an office", aspect="16:9")
        proposal = approvals.propose(db, agent="content_community", action_type="social.post", title="HR", payload={"networks": ["linkedin"], "kind": "image", "text": "Bewerbungen (direkt)", "media_ids": [made.id]})
        approvals.decide(db, [proposal], "approve", admin_id=1)
        approvals.execute_due(db)
        row = db.execute(select(SocialPost)).scalar_one()
        assert posts.publish(db, row)["url"] == "https://www.linkedin.com/feed/update/urn:li:share:7001/"
    body = next(c for c in fake.calls if c[1].endswith("/rest/posts"))[2]
    assert body["author"] == "urn:li:organization:4242" and body["content"]["media"]["id"] == "urn:li:image:I1" and body["commentary"] == "Bewerbungen \\(direkt\\)"


# --- connecting ---------------------------------------------------------------------------------------------
def test_connecting_meta_keeps_the_page_and_its_instagram_with_encrypted_tokens(fake: FakeNetworks, admin: Any) -> None:  # noqa: F811 - the fixture
    assert admin.post("/admin-api/marketing/connect/meta").status_code in (400, 422), "the app first"
    saved = admin.put("/admin-api/marketing/apps/meta", json={"client_id": "1234567", "client_secret": "s3cret"}).json()
    assert saved["secret_set"] and saved["redirect_uri"] == "https://admin.tower.example/admin-api/marketing/connect/meta/callback" and "s3cret" not in json.dumps(saved)
    url = admin.post("/admin-api/marketing/connect/meta").json()["url"]
    query = parse_qs(urlparse(url).query)
    assert url.startswith("https://www.facebook.com/") and "instagram_content_publish" in query["scope"][0]
    state = query["state"][0]
    admin.put("/admin-api/marketing/apps/meta", json={"client_id": "1234567", "config_id": "998877"})
    business = parse_qs(urlparse(admin.post("/admin-api/marketing/connect/meta").json()["url"]).query)
    assert business["config_id"] == ["998877"] and "scope" not in business, "Facebook Login for Business: the configuration, not a scope list"
    assert admin.get("/admin-api/marketing").json()["apps"][0]["secret_set"], "saving the configuration keeps the secret"
    back = admin.get("/admin-api/marketing/connect/meta/callback", params={"code": "c0de", "state": state}, follow_redirects=False)
    assert back.status_code == 303 and "connected=" in back.headers["location"]
    again = admin.get("/admin-api/marketing/connect/meta/callback", params={"code": "c0de", "state": state}, follow_redirects=False)
    assert "error=" in again.headers["location"], "a state works once"
    with _db() as db:
        page = db.execute(select(SocialAccount).where(SocialAccount.network == "facebook")).scalar_one()
        ig = db.execute(select(SocialAccount).where(SocialAccount.network == "instagram")).scalar_one()
        assert ig.parent_id == page.id and page.followers == 120 and "page-token" not in page.token_enc
        assert accounts.token(db, ig) == "page-token", "Instagram publishes with its Page's token"
        page_id = page.id
    overview = admin.get("/admin-api/marketing").json()
    assert {a["network"] for a in overview["accounts"]} == {"facebook", "instagram"} and all("token" not in k for a in overview["accounts"] for k in a if k != "token_expires_at")
    assert admin.delete(f"/admin-api/marketing/accounts/{page_id}").status_code == 204
    assert admin.get("/admin-api/marketing").json()["accounts"] == [], "the Page's Instagram goes with it"


# --- the admin ------------------------------------------------------------------------------------------------
def test_the_owner_runs_marketing_from_the_admin(fake: FakeNetworks, admin: Any) -> None:  # noqa: F811 - the fixture
    out = admin.put("/admin-api/marketing/settings", json={"monthly_picture_budget_usd": 3, "picture_quality": "standard", "posting_hours": [9, 18]}).json()
    assert out["budget"]["monthly_budget_usd"] == 3 and out["settings"]["posting_hours"] == [9, 18]
    made = admin.post("/admin-api/marketing/media/image", json={"prompt": "A chef in a hotel kitchen in Bamberg, plating", "aspect": "1:1", "shows_people": True}).json()
    assert made["ai_generated"] and admin.get(made["url"]).headers["content-type"] == "image/jpeg"
    up = admin.post("/admin-api/marketing/media/upload", files={"file": ("logo.png", _png(200, 80), "image/png")}).json()
    assert up["source"] == "upload" and not up["ai_generated"]
    assert admin.post("/admin-api/marketing/media/upload", files={"file": ("x.png", b"not a picture", "image/png")}).status_code in (400, 422)
    kit = admin.get("/admin-api/marketing").json()["brand"]
    assert admin.put("/admin-api/marketing/brand", json={**kit, "logo_media_id": up["id"], "voice": "Klar, freundlich, du für Bewerber."}).status_code == 200
    assert admin.delete(f"/admin-api/marketing/media/{up['id']}").status_code in (400, 422), "the logo stays while it is the logo"
    link = admin.post("/admin-api/marketing/media/video", json={"link": "https://drive.google.com/file/d/XYZ/view", "title": "Clip"}).json()
    assert link["kind"] == "video" and "uc?export=download" in link["link"]
    from fastapi.testclient import TestClient

    from app.main import app as api_app

    with _db() as db:
        token = db.get(MarketingMedia, made["id"]).public_token  # type: ignore[union-attr]
    with TestClient(api_app) as public:
        picture = public.get(f"/api/v1/media/{token}.jpg")
        assert picture.status_code == 200 and picture.headers["content-type"] == "image/jpeg"
        assert public.get("/api/v1/media/not-a-token-at-all-really.jpg").status_code == 404
    assert admin.get("/admin-api/marketing/results").status_code == 200 and admin.get("/admin-api/marketing/plans").json() == []


def test_a_post_waiting_by_hand_is_published_once_its_network_is_connected(fake: FakeNetworks, admin: Any) -> None:  # noqa: F811 - the fixture
    with _db() as db:
        platform.put(db, "social", {"networks": ["facebook", "instagram"]})
        fb = SocialPost(network="facebook", kind="text", text="Hallo Kulmbach", status="ready", created_at=datetime.now(UTC))
        ig = SocialPost(network="instagram", kind="text", text="Nur Text", status="ready", created_at=datetime.now(UTC))
        db.add_all([fb, ig])
        db.flush()
        fb_id, ig_id = fb.id, ig.id
    listed = {p["id"]: p for p in admin.get("/admin-api/marketing/posts").json()}
    assert listed[fb_id]["can_publish"] is False, "not connected yet"
    with _db() as db:
        _connect_meta(db)
    listed = {p["id"]: p for p in admin.get("/admin-api/marketing/posts").json()}
    assert listed[fb_id]["can_publish"] is True and listed[ig_id]["can_publish"] is False, "Instagram needs a picture"
    assert admin.post(f"/admin-api/marketing/posts/{ig_id}/publish-now").status_code in (400, 422)
    out = admin.post(f"/admin-api/marketing/posts/{fb_id}/publish-now").json()
    assert out["status"] == "scheduled"
    with _db() as db:
        row = db.get(SocialPost, fb_id)
        assert row is not None and posts.publish(db, row).get("posted") and row.external_id == "111_556"


def test_publish_now_moves_the_waiting_job_to_now_not_only_the_post(fake: FakeNetworks, admin: Any) -> None:  # noqa: F811 - the fixture
    from datetime import timedelta

    from app.models import AgentTask

    with _db() as db:
        platform.put(db, "social", {"networks": ["facebook"]})
        _connect_meta(db)
        later = datetime.now(UTC) + timedelta(days=4)
        post = SocialPost(network="facebook", kind="text", text="Hallo Kulmbach", status="scheduled", scheduled_at=later, created_at=datetime.now(UTC))
        db.add(post)
        db.flush()
        posts.schedule(db, post)
        post_id = post.id
    assert admin.post(f"/admin-api/marketing/posts/{post_id}/publish-now").status_code == 200
    with _db() as db:
        jobs = list(db.execute(select(AgentTask).where(AgentTask.dedupe_key == f"social.publish:{post_id}", AgentTask.status == "queued")).scalars())
        assert len(jobs) == 1, "one job per post"
        assert jobs[0].run_at <= datetime.now(UTC) + timedelta(seconds=5), f"the job still waits for {jobs[0].run_at}: Publish now did nothing"
