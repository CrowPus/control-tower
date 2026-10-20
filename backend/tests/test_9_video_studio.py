"""The video studio: Acme's character, the Graphic Designer's concepts checked
against the owner's video budget and videos per day or week, a video made scene by scene only after the
owner's approval (the video model answered here by a fake, the scenes joined by the real ffmpeg), the Social
Media Manager told it is ready, posting it on TikTok and Instagram, and the owner deleting it once posted."""

from __future__ import annotations

import io
import json
import re
import subprocess
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from PIL import Image
from sqlalchemy import delete, select

from app.config import settings
from app.core import crypto
from app.db import transaction
from app.models import (
    AIUsage,
    MarketingMedia,
    MarketingVideo,
    SocialAccount,
    SocialPost,
    TeamApproval,
    TeamEvent,
)
from app.services import platform
from app.services.marketing import brand, character, plans, studio, video_studio
from app.services.marketing import tasks as video_tasks
from app.services.social import accounts, meta, net, posts, tiktok
from app.services.team import approvals, events, registry, tools
from tests.test_9_team import admin  # noqa: F401 - the owner signed in to the admin app (fixture)


def _db() -> Any:
    return transaction(write=True)


def _png(color: tuple[int, int, int] = (200, 150, 120)) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (600, 800), color).save(out, format="PNG")
    return out.getvalue()


def _clip(seconds: float = 1.0, color: str = "blue") -> bytes:
    """A small real video with sound, as the video model would give it (vertical, 720x1280)."""
    path = Path(settings().marketing_dir) / f"fake-{color}.mp4"
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([video_studio.ffmpeg(), "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"color=c={color}:s=720x1280:d={seconds}:r=24",  # noqa: S603 - our own arguments
                    "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}", "-shortest", "-c:v", "libx264", "-preset", "ultrafast",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", str(path)], check=True, capture_output=True)
    data = path.read_bytes()
    path.unlink()
    return data


class FakeVeo:
    """The video model: starts scenes, answers when they are made, gives the files."""

    def __init__(self) -> None:
        self.started: list[dict[str, Any]] = []
        self.ready: set[str] = set()
        self.declined: set[str] = set()
        self.clip = _clip()

    def start(self, prompt: str, model: str, references: Any, seconds: int, frame: Any = None) -> str:
        name = f"operations/op-{len(self.started) + 1}"
        self.started.append({"name": name, "prompt": prompt, "model": model, "references": len(list(references)), "seconds": seconds, "frame": frame})
        return name

    def poll(self, name: str) -> video_studio.Poll:
        if name in self.declined:
            return video_studio.Poll(done=True, error="Not made: the scene was blocked")
        if name in self.ready:
            return video_studio.Poll(done=True, uri=f"https://generativelanguage.example/{name}.mp4")
        return video_studio.Poll(done=False)

    def download(self, uri: str) -> bytes:
        return self.clip


class FakeTikTok:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, Any], dict[str, str]]] = []
        self.options = ["PUBLIC_TO_EVERYONE", "SELF_ONLY"]

    def __call__(self, request: httpx.Request) -> httpx.Response:
        url = urlparse(str(request.url))
        body: dict[str, Any] = {}
        if request.content and request.headers.get("content-type", "").startswith("application/json"):
            body = json.loads(request.content)
        elif request.content and request.headers.get("content-type", "").startswith("application/x-www-form-urlencoded"):
            body = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
        self.calls.append((request.method, f"{url.netloc}{url.path}", body, dict(request.headers)))
        host, path = url.netloc, url.path
        if host == "open.tiktokapis.com":
            if path == "/v2/oauth/token/":
                return httpx.Response(200, json={"access_token": "tt-token", "refresh_token": "tt-refresh", "expires_in": 86400, "open_id": "tt-open-1"})
            if path == "/v2/user/info/":
                return httpx.Response(200, json={"data": {"user": {"open_id": "tt-open-1", "display_name": "Acme", "username": "acme", "follower_count": 88}},
                                                 "error": {"code": "ok"}})
            if path == "/v2/post/publish/creator_info/query/":
                return httpx.Response(200, json={"data": {"privacy_level_options": self.options}, "error": {"code": "ok"}})
            if path == "/v2/post/publish/video/init/":
                return httpx.Response(200, json={"data": {"publish_id": "pub-1", "upload_url": "https://upload.tiktok.example/u1?x=1"}, "error": {"code": "ok"}})
            if path == "/v2/post/publish/status/fetch/":
                return httpx.Response(200, json={"data": {"status": "PUBLISH_COMPLETE", "publicaly_available_post_id": [7300000001]}, "error": {"code": "ok"}})
            if path == "/v2/video/query/":
                return httpx.Response(200, json={"data": {"videos": [{"id": "7300000001", "view_count": 1500, "like_count": 90, "comment_count": 4, "share_count": 7}]},
                                                 "error": {"code": "ok"}})
        if host == "upload.tiktok.example":
            return httpx.Response(201)
        if host == "graph.facebook.com":
            if path.endswith("/999/media"):
                return httpx.Response(200, json={"id": "container-1"})
            if path.endswith("/container-1"):
                return httpx.Response(200, json={"status_code": "FINISHED"})
            if path.endswith("/999/media_publish"):
                return httpx.Response(200, json={"id": "ig-media-1"})
            if path.endswith("/ig-media-1"):
                return httpx.Response(200, json={"permalink": "https://www.instagram.com/reel/R1/"})
        return httpx.Response(404, json={"error": {"message": f"unknown {host}{path}"}})


@pytest.fixture
def veo(monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeVeo]:
    fake = FakeVeo()
    monkeypatch.setattr(video_studio, "starter", fake.start)
    monkeypatch.setattr(video_studio, "poller", fake.poll)
    monkeypatch.setattr(video_studio, "downloader", fake.download)
    monkeypatch.setattr(studio, "generator", lambda prompt, aspect, model, *refs: studio.Made(data=_png((90, 90, 90)), mime_type="image/png", model=model))
    monkeypatch.setattr(video_studio, "reviewer", lambda video, script, facts: ({"said": "Hundert Bewerbungen und keine Antwort? Bei Role three-sixty-five …",
                                                                                 "brand_in_english": True, "mistakes": [], "false_claims": [], "verdict": "post",
                                                                                 "summary": "Clean."}, "gemini-test", 1000, 100))
    yield fake


@pytest.fixture
def networks(monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeTikTok]:
    fake = FakeTikTok()
    monkeypatch.setattr(net, "transport", httpx.MockTransport(fake))
    monkeypatch.setattr(meta, "sleep_seconds", 0)
    monkeypatch.setattr(settings(), "public_base_url", "https://app.tower.example")
    monkeypatch.setattr(settings(), "admin_public_base_url", "https://admin.tower.example")
    yield fake


@pytest.fixture(autouse=True)
def clean() -> Iterator[None]:
    yield
    with _db() as db:
        for model in (SocialPost, MarketingVideo, SocialAccount, MarketingMedia, TeamEvent, TeamApproval):
            db.execute(delete(model))
        FRAMES.clear()
        db.execute(delete(AIUsage).where(AIUsage.operation.in_((studio.OPERATION, video_studio.OPERATION, video_studio.REVIEW_OPERATION))))
        for key in ("brand", "marketing", "social", "social_apps", "character", accounts.STATE_KEY):
            platform.put(db, key, {})


FRAMES: list[int] = []


def _setup(db: Any, *, budget: float | None = 5.0, count: int | None = 2, per: str = "week") -> character.Character:
    pictures = [studio.upload(db, data=_png((200, 150, 100 + i)), filename=f"mila-{i}.png", title=f"Mila {i}") for i in range(2)]
    figure = character.save(db, character.Character(name="Mila", who="Acme's guide to good jobs, late twenties", look="short curly black hair, round glasses",
                                                     voice="warm, quick, a little cheeky", rules="always waves at the start",
                                                     picture_ids=[p.id for p in pictures]))
    brand.save_settings(db, brand.MarketingSettings(monthly_picture_budget_usd=5, monthly_video_budget_usd=budget, videos_count=count, videos_per=per))
    first = studio.frame(db, moment="Mila at the kitchen table, looking into the camera", outfit="grey hoodie", setting="a small kitchen in Kulmbach")
    second = studio.frame(db, moment="Mila leans towards the camera and smiles", outfit="grey hoodie", setting="a small kitchen in Kulmbach", continue_from=first.id)
    FRAMES[:] = [first.id, second.id]
    return figure


def _concept(**changes: Any) -> dict[str, Any]:
    concept = {
        "title": "100 Bewerbungen, null Antworten?", "series": "Mila hilft", "idea": "Mila sitzt vor einem Stapel Absagen, dreht sich zur Kamera und zeigt, "
        "wie man sich bei Acme direkt bei geprüften Arbeitgebern bewirbt.", "why_it_works": "relatable pain, quick payoff", "audience": "job seekers 20-35",
        "hook": "Mila wirft einen Stapel Papier in die Luft", "setting": "a small flat in Kulmbach, morning light", "outfit": "grey hoodie, teal scarf", "uses_character": True,
        "scenes": [{"seconds": 8, "shot": "Medium shot, Mila at a kitchen table covered with letters, morning light through the window", "action": "throws papers up",
                    "expression": "wide-eyed despair, then a slow sigh", "sound": "a sad violin, paper rustling"},
                   {"seconds": 8, "shot": "Close-up of Mila holding her phone, smiling into the camera in the same kitchen", "action": "shows the phone",
                    "expression": "a slow, triumphant grin", "sound": "a soft ping, upbeat music"}],
        "end_card": {"headline": "Direkt bewerben", "subline": "bei geprüften Arbeitgebern"}, "caption": "Kennst du das? 👀 #jobsuche", "hashtags": ["#jobsuche"],
        "networks": ["tiktok", "instagram"],
    }
    for scene, frame in zip(concept["scenes"], FRAMES, strict=False):
        scene["start_frame"] = frame
    concept.update(changes)
    return concept


def _approve(db: Any, concept: dict[str, Any]) -> MarketingVideo:
    proposal = approvals.propose(db, agent="graphic_designer", action_type="video.concept", title=concept["title"], payload=concept)
    approvals.decide(db, [proposal], "approve", admin_id=1)
    approvals.execute_due(db)
    assert proposal.status == "executed", proposal.result
    return db.execute(select(MarketingVideo).where(MarketingVideo.approval_id == proposal.id)).scalar_one()


def _make(db: Any, row: MarketingVideo, veo: FakeVeo) -> None:
    """Scenes are made one after another: each look makes the started one and starts the next."""
    for _ in range(12):
        for item in veo.started:
            veo.ready.add(item["name"])
        if not video_studio.step(db, row):
            return
    raise AssertionError("the video was never finished")


# --- the team ---------------------------------------------------------------------------------------------------
def test_the_graphic_designer_is_in_marketing_and_every_concept_is_the_owners() -> None:
    designer = registry.AGENTS["graphic_designer"]
    assert designer.department == "Marketing" and registry.actions_of(designer) == ("playbook.change", "video.concept") or "video.concept" in registry.actions_of(designer)
    assert registry.ACTIONS["video.concept"].ceiling == "L3", "the owner approves each concept before a video is made"
    assert {"character", "video_studio", "create_image", "web_search"} <= set(designer.tools) and "marketing.brief" in designer.subscribes
    social = registry.AGENTS["content_community"]
    assert "video.ready" in social.subscribes and "video_studio" in social.tools
    assert "graphic_designer" in plans.SPECIALISTS and "video_studio" in registry.AGENTS["marketing_lead"].tools


# --- concepts are checked when proposed --------------------------------------------------------------------------
def test_a_concept_needs_the_character_the_budget_and_the_owners_number_of_videos(veo: FakeVeo) -> None:
    with _db() as db:
        refused = " ".join(video_studio.check(db, _concept()))
        assert "character is not set up" in refused and "video budget" in refused and "how many videos" in refused
        _setup(db, budget=1.0, count=1)
        assert "would cost about $1.60" in " ".join(video_studio.check(db, _concept())), "two 8-second scenes at the fast price"
        brand.save_settings(db, brand.MarketingSettings(monthly_video_budget_usd=5, videos_count=1, videos_per="week"))
        assert "8 seconds long" in " ".join(video_studio.check(db, _concept(scenes=[{**_concept()["scenes"][0], "seconds": 4}])))
        assert "1 to 5 scenes" in " ".join(video_studio.check(db, _concept(scenes=[])))
        assert "does not speak" in " ".join(video_studio.check(db, _concept(scenes=[{**_concept()["scenes"][0], "dialogue": "Hallo!"}]))), "the owner's silent character"
        character.save(db, character.get(db).model_copy(update={"speaks": True}))
        assert "too many words" in " ".join(video_studio.check(db, _concept(scenes=[{**_concept()["scenes"][0], "dialogue": "wort " * 40}])))
        assert "does not speak" not in " ".join(video_studio.check(db, _concept(scenes=[{**_concept()["scenes"][0], "dialogue": "Hallo!"}])))
        character.save(db, character.get(db).model_copy(update={"speaks": False}))
        first = approvals.propose(db, agent="graphic_designer", action_type="video.concept", title="Eins", payload=_concept())
        assert first.status == "pending" and first.level == "L3"
        with pytest.raises(approvals.ProposalRefused, match="already has its 1 video"):
            approvals.propose(db, agent="graphic_designer", action_type="video.concept", title="Zwei", payload=_concept(title="Zwei"))
        assert video_studio.rhythm(db)["left"] == 0 and video_studio.rhythm(db, leave_out=first.id)["left"] == 1


# --- making it ----------------------------------------------------------------------------------------------------
def test_an_approved_concept_is_made_scene_by_scene_with_the_character_and_the_social_media_manager_hears_of_it(veo: FakeVeo) -> None:
    with _db() as db:
        brand.save_kit(db, brand.BrandKit(image_style="warm, real, bright"))
        _setup(db)
        row = _approve(db, _concept())
        assert row.status == "making" and row.model == settings().gemini_video_model
        assert db.execute(select(func_count()).select_from(_tasks()).where(_tasks().task_type == "marketing.video_step")).scalar() == 1
        assert video_studio.step(db, row)
        assert len(veo.started) == 1 and veo.started[0]["frame"] and veo.started[0]["seconds"] == 8, "scene 1 starts from its approved storyboard frame"
        assert video_studio.step(db, row) and len(veo.started) == 1, "scene 2 waits for scene 1 (it continues from its last picture)"
        veo.ready.add(veo.started[0]["name"])
        assert video_studio.step(db, row) and len(veo.started) == 2 and [s["state"] for s in row.progress["scenes"]] == ["done", "wait"]
        frame = veo.started[1]["frame"]
        assert frame and frame[1] == "image/png" and frame[0][:8] == b"\x89PNG\r\n\x1a\n", "scene 2 starts from the last picture of scene 1"
        first, second = veo.started[0]["prompt"], veo.started[1]["prompt"]
        assert video_studio.CONTINUE in second and video_studio.CONTINUE not in first
        assert "silent-film" not in first and "silent film" not in first and "no title cards" in first, "no old-film title cards with made-up words"
        assert "Mila" in first and "short curly black hair" in first and "No text" in first and "grey hoodie, teal scarf" in first and "a small flat in Kulmbach" in first
        assert video_studio.SILENT.format(name="Mila") in first and "says" not in first and "voice" not in first.lower().split("no voices")[0], "a silent character"
        assert "wide-eyed despair" in first and video_studio.SIMPLE in first
        assert "always waves" not in first, "the owner's rules are the agent's, not words for the video model to say"
        assert "Scene 1 of 2" in first and "Scene 2 of 2" in second
        assert video_studio.step(db, row) and len(veo.started) == 2, "waiting scenes are never started twice"
        veo.ready.add(veo.started[1]["name"])
        assert not video_studio.step(db, row)
        assert row.status == "ready", row.error
        media = db.get(MarketingMedia, row.media_id)
        assert media is not None and media.kind == "video" and media.source == "generated" and media.ai_generated and media.width == 720 and media.height == 1280
        path = video_studio.file_of(media)
        assert path is not None and path.stat().st_size == media.size and media.data and media.data[:2] == b"\xff\xd8", "the file and its cover picture"
        probe = subprocess.run([video_studio.ffmpeg(), "-hide_banner", "-i", str(path)], capture_output=True, text=True).stderr  # noqa: S603 - our own arguments
        duration = re.search(r"Duration: (\d+):(\d+):([\d.]+)", probe)
        assert duration and 4.0 <= float(duration.group(3)) <= 5.0, "two 1-second scenes and the 2.5-second end card"
        assert "1280" in probe and "Audio:" in probe
        cost = db.execute(select(AIUsage).where(AIUsage.operation == video_studio.OPERATION)).scalars().all()
        assert sum(u.estimated_cost_usd for u in cost) == pytest.approx(1.6) and row.cost_usd == pytest.approx(1.6)
        assert (Path(settings().marketing_dir) / "work" / str(row.id) / "scene-1.mp4").exists(), "the made scenes are kept (a scene can be made again)"
        frame = subprocess.run([video_studio.ffmpeg(), "-loglevel", "error", "-ss", "0.5", "-i", str(path), "-frames:v", "1", "-f", "image2pipe", "-vcodec", "png", "-"],  # noqa: S603 - our own arguments
                               capture_output=True, check=True).stdout
        top = Image.open(io.BytesIO(frame)).convert("RGB").getpixel((360, 280))
        assert top[2] > 150 and top[0] < 80, f"nothing is laid over the picture (got {top})"
        assert row.progress["review"] == {"state": "waiting"}
        assert not [e for e in events.unseen(db, "content_community", 20)[0] if e.type == "video.ready"], "told only after the check"
        video_id, media_id = row.id, media.id
    assert video_studio.review_now(video_id, _db)["review"] == "post"
    with _db() as db:
        row = db.get(MarketingVideo, video_id)
        assert row is not None and row.progress["review"]["verdict"] == "post" and row.progress["review"]["brand_in_english"] is True
        assert db.execute(select(AIUsage).where(AIUsage.operation == video_studio.REVIEW_OPERATION)).scalars().first() is not None
        ready, _ = events.unseen(db, "content_community", 20)
        told = [e for e in ready if e.type == "video.ready"]
        assert told and told[0].payload["media_id"] == media_id and told[0].payload["networks"] == ["tiktok", "instagram"]
        assert told[0].payload["review"]["verdict"] == "post"
        assert not [e for e in events.unseen(db, "trust_compliance", 20)[0] if e.type == "video.ready"], "only the Social Media Manager hears it"


def test_a_speaking_character_says_exactly_its_words_and_the_name_as_the_owner_set() -> None:
    figure = character.Character(name="Mila", look="curly hair", voice="warm", rules="always waves", speaks=True, picture_ids=[1])
    concept = {"idea": "x", "outfit": "grey hoodie", "setting": "a kitchen", "scenes": [{"seconds": 8, "shot": "close-up", "dialogue": "Hundert Bewerbungen?"},
                                                                                       {"seconds": 8, "shot": "close-up", "dialogue": "Bei Acme bewirbst du dich direkt."}]}
    kit = brand.BrandKit(name="Acme", pronunciation="AK-mee")
    first, second = (video_studio.scene_prompt(concept, i, figure, "", from_frame=True, kit=kit) for i in (0, 1))
    said = 'The name "Acme" is said "AK-mee"'
    assert 'exactly these words and nothing else: "Hundert Bewerbungen?"' in first and "Mila's voice: warm" in first and said not in first
    assert '"Bei Acme bewirbst du dich direkt."' in second and said in second, "the name is said the owner's way"
    assert "by Acme" in second and said not in video_studio.scene_prompt(concept, 1, figure, "", from_frame=True, kit=brand.BrandKit(name="Acme")), "no hint unless given"
    assert "SILENT" not in video_studio.script_of(concept) and "SILENT" in video_studio.script_of(concept, silent=True)


def test_the_check_says_fix_for_ai_mistakes_false_claims_or_the_name_in_german(veo: FakeVeo, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, str] = {}

    def strict(video: bytes, script: str, facts: str) -> Any:
        seen.update(script=script, facts=facts)
        return ({"said": "Ep liv … guaranteed visa", "brand_in_english": False, "verdict": "post",
                 "mistakes": [{"at": "0:07", "what": "the jacket changes between the scenes", "severity": "high"}],
                 "false_claims": ["guaranteed visa — nobody can promise a visa"], "summary": "Two problems."}, "gemini-test", 900, 90)

    monkeypatch.setattr(video_studio, "reviewer", strict)
    with _db() as db:
        brand.save_kit(db, brand.BrandKit(tagline="Direkt bewerben bei geprüften Arbeitgebern",
                                          facts_for_customers=["Acme checks every opportunity on the organiser's official page; you apply yourself."]))
        _setup(db)
        row = _approve(db, _concept())
        _make(db, row, veo)
        video_id = row.id
    video_studio.review_now(video_id, _db)
    assert "Acme checks every opportunity" in seen["facts"] and "wide-eyed despair" in seen["script"], "the check knows the script and the facts"
    assert seen["script"].startswith("SILENT") and "End card: Direkt bewerben" in seen["script"], "any voice in a silent video is a mistake"
    with _db() as db:
        review = db.get(MarketingVideo, video_id).progress["review"]  # type: ignore[union-attr]
        assert review["verdict"] == "fix", "a high mistake, a false claim or the name said wrongly: not to be posted as it is"
        told = [e for e in events.unseen(db, "content_community", 20)[0] if e.type == "video.ready"]
        assert told[0].payload["review"]["verdict"] == "fix" and told[0].payload["review"]["false_claims"]


def test_a_declined_scene_is_not_paid_and_trying_again_keeps_the_made_scenes(veo: FakeVeo) -> None:
    with _db() as db:
        _setup(db)
        row = _approve(db, _concept())
        video_studio.step(db, row)
        veo.ready.add(veo.started[0]["name"])
        video_studio.step(db, row)
        veo.declined.add(veo.started[1]["name"])
        assert not video_studio.step(db, row)
        assert row.status == "failed" and "blocked" in row.error and row.cost_usd == pytest.approx(0.8)
        video_studio.again(db, row)
        assert row.status == "making" and [s["state"] for s in row.progress["scenes"]] == ["done", "start"]
        video_studio.step(db, row)
        assert len(veo.started) == 3, "only the failed scene is made again"
        veo.ready.add(veo.started[2]["name"])
        assert not video_studio.step(db, row) and row.status == "ready" and row.cost_usd == pytest.approx(1.6)


def test_a_video_stops_when_the_budget_runs_out_and_a_lost_look_is_picked_up(veo: FakeVeo) -> None:
    with _db() as db:
        _setup(db, budget=2.0)
        row = _approve(db, _concept())
        db.add(AIUsage(agent_type="team", model="x", operation=video_studio.OPERATION, input_tokens=0, output_tokens=0, estimated_cost_usd=1.0,
                       latency_ms=0, success=True, error_class="", created_at=datetime.now(UTC)))
        db.flush()
        video_studio.step(db, row)
        assert [s["state"] for s in row.progress["scenes"]] == ["wait", "start"], "scene 2 waits for scene 1"
        veo.ready.add(veo.started[0]["name"])
        video_studio.step(db, row)
        assert [s["state"] for s in row.progress["scenes"]] == ["done", "failed"] and "budget is used up" in row.progress["scenes"][1]["error"]
        other = MarketingVideo(title="Lost", concept=_concept(), status="making", progress={"scenes": [{"state": "start"}]}, created_at=datetime.now(UTC) - timedelta(hours=5))
        db.add(other)
        db.flush()
        assert row.status == "failed", "scene 2 could not be paid: the video stops"
        assert video_tasks.pick_up(db) == 0 and other.status == "failed", "one far too old is stopped"


def test_a_video_with_mistakes_is_made_again_from_the_bad_scene_on(veo: FakeVeo) -> None:
    with _db() as db:
        _setup(db)
        row = _approve(db, _concept())
        _make(db, row, veo)
        assert row.status == "ready"
        old_media = row.media_id
        row.progress = {**row.progress, "review": {"state": "done", "verdict": "fix", "mistakes": [
            {"at": "0:08", "what": "Glasses disappear completely between scenes.", "severity": "high"},
            {"at": "0:12", "what": "Garbled text overlay appears on screen.", "severity": "high"}]}}
        assert video_studio.mistakes_by_scene(row.concept, row.progress["review"]) == {1: ["Glasses disappear completely between scenes.", "Garbled text overlay appears on screen."]}
        assert video_studio.view(db, row)["remake_from"] == 2, "a mistake right at a cut belongs to the scene that starts there"
        with pytest.raises(video_studio.VideoRefused, match="from 1 to 2"):
            video_studio.remake(db, row, 3)
        assert video_studio.remake(db, row, 2) == pytest.approx(0.8), "only scene 2 is made (and paid) again"
        assert row.status == "making" and [s["state"] for s in row.progress["scenes"]] == ["done", "start"]
        started = len(veo.started)
        _make(db, row, veo)
        assert len(veo.started) == started + 1 and row.status == "ready" and row.cost_usd == pytest.approx(2.4)
        prompt = veo.started[-1]["prompt"]
        assert "Last time this scene went wrong" in prompt and "Glasses disappear" in prompt and video_studio.CONTINUE in prompt
        assert row.media_id != old_media and db.get(MarketingMedia, old_media) is None, "the old video goes once the new one is made"
        assert row.progress["review"] == {"state": "waiting"}, "the new video is checked again"


def test_a_video_made_before_the_scenes_were_kept_is_cut_from_the_finished_file(veo: FakeVeo) -> None:
    with _db() as db:
        _setup(db)
        row = _approve(db, _concept())
        _make(db, row, veo)
        work = Path(settings().marketing_dir) / "work" / str(row.id)
        for f in work.glob("scene-*.mp4"):
            f.unlink()
        row.progress = {**row.progress, "review": {"state": "done", "verdict": "fix", "mistakes": [{"at": "0:09", "what": "The mug vanishes.", "severity": "high"}]}}
        video_studio.remake(db, row, 2)
        assert (work / "scene-1.mp4").exists() and row.progress["scenes"][0]["state"] == "done"


# --- posting and deleting -----------------------------------------------------------------------------------------
def _tiktok(db: Any) -> SocialAccount:
    now = datetime.now(UTC)
    account = SocialAccount(network="tiktok", external_id="tt-open-1", name="Acme", token_expires_at=now + timedelta(hours=20), created_at=now, updated_at=now)
    db.add(account)
    db.flush()
    account.token_enc = crypto.encrypt("tt-token", associated_data="social_account:tiktok:tt-open-1:token")
    page = SocialAccount(network="facebook", external_id="111", name="Acme", created_at=now, updated_at=now)
    db.add(page)
    db.flush()
    page.token_enc = crypto.encrypt("page-token", associated_data="social_account:facebook:111:token")
    db.add(SocialAccount(network="instagram", external_id="999", name="the website", parent_id=page.id, created_at=now, updated_at=now))
    db.flush()
    return account


def test_the_finished_video_goes_out_on_tiktok_and_instagram_and_the_owner_deletes_it_afterwards(veo: FakeVeo, networks: FakeTikTok, admin: Any) -> None:  # noqa: F811 - the fixture
    with _db() as db:
        _setup(db)
        _tiktok(db)
        row = _approve(db, _concept())
        _make(db, row, veo)
        video_id, media_id = row.id, row.media_id
        with pytest.raises(approvals.ProposalRefused, match="storyboard frame"):
            approvals.propose(db, agent="content_community", action_type="social.post", title="Frame",
                              payload={"networks": ["instagram"], "kind": "image", "text": "Mila!", "media_ids": [FRAMES[0]]})
        token = db.get(MarketingMedia, media_id).public_token
        post = approvals.propose(db, agent="content_community", action_type="social.post", title="Video",
                                 payload={"networks": ["tiktok", "instagram"], "kind": "video", "text": "Kennst du das? 👀 #jobsuche", "media_ids": [media_id]})
        approvals.decide(db, [post], "approve", admin_id=1)
        approvals.execute_due(db)
        rows = {r.network: r for r in db.execute(select(SocialPost).where(SocialPost.approval_id == post.id)).scalars()}
        assert {r.status for r in rows.values()} == {"scheduled"}, "a made video needs nothing from the owner"
    listed = admin.get("/admin-api/marketing/videos").json()
    assert listed[0]["status"] == "ready" and listed[0]["file_url"].endswith(f"/media/{media_id}/video") and len(listed[0]["posts"]) == 2
    assert admin.get(listed[0]["file_url"]).headers["content-type"] == "video/mp4"
    assert admin.get(listed[0]["cover_url"]).headers["content-type"] == "image/jpeg"
    assert admin.delete(f"/admin-api/marketing/videos/{video_id}").status_code in (400, 422), "posts not out yet use it"
    from fastapi.testclient import TestClient

    from app.main import app as api_app

    with TestClient(api_app) as public:
        served = public.get(f"/api/v1/media/{token}.mp4")
        assert served.status_code == 200 and served.headers["content-type"] == "video/mp4"
    with _db() as db:
        for one in db.execute(select(SocialPost)).scalars().all():
            assert posts.publish(db, one).get("posted"), one.error
        done = {r.network: r for r in db.execute(select(SocialPost)).scalars()}
        assert done["tiktok"].post_url == "https://www.tiktok.com/video/7300000001" and done["instagram"].post_url == "https://www.instagram.com/reel/R1/"
        assert posts.collect_metrics(db)["posts"] >= 1 and done["tiktok"].metrics["views"] == 1500
    init = next(c for c in networks.calls if c[1].endswith("/video/init/"))
    assert init[2]["post_info"]["is_aigc"] is True and init[2]["post_info"]["privacy_level"] == "PUBLIC_TO_EVERYONE"
    assert init[2]["source_info"]["source"] == "FILE_UPLOAD" and init[2]["source_info"]["total_chunk_count"] == 1
    upload = next(c for c in networks.calls if c[1].startswith("upload.tiktok.example"))
    size = init[2]["source_info"]["video_size"]
    assert upload[3]["content-range"] == f"bytes 0-{size - 1}/{size}"
    reel = next(c for c in networks.calls if c[1].endswith("/999/media"))
    assert reel[2]["video_url"] == f"https://app.tower.example/api/v1/media/{token}.mp4", "Instagram fetches the made video from its public address"
    gone = admin.delete(f"/admin-api/marketing/videos/{video_id}").json()
    assert gone["status"] == "deleted" and gone["media_id"] is None and gone["concept"]["title"], "the concept stays for the record"
    with _db() as db:
        assert db.get(MarketingMedia, media_id) is None and not list((Path(settings().marketing_dir) / "videos").glob(f"{token}*"))
        assert {r.status for r in db.execute(select(SocialPost)).scalars()} == {"posted"}, "the posts on the networks stay"
    with TestClient(api_app) as public:
        assert public.get(f"/api/v1/media/{token}.mp4").status_code == 404


def test_tiktok_connects_with_the_owners_app_and_renews_its_sign_in(networks: FakeTikTok, admin: Any) -> None:  # noqa: F811 - the fixture
    saved = admin.put("/admin-api/marketing/apps/tiktok", json={"client_id": "awkey123", "client_secret": "tt-secret"}).json()
    assert saved["secret_set"] and saved["redirect_uri"].endswith("/connect/tiktok/callback")
    url = admin.post("/admin-api/marketing/connect/tiktok").json()["url"]
    query = parse_qs(urlparse(url).query)
    assert url.startswith("https://www.tiktok.com/v2/auth/authorize/") and "video.publish" in query["scope"][0] and query["client_key"] == ["awkey123"]
    with _db() as db:
        rows = accounts.finish(db, "tiktok", "the-code", 1)
        assert [(r.network, r.name, r.followers) for r in rows] == [("tiktok", "Acme", 88)]
        rows[0].token_expires_at = datetime.now(UTC) - timedelta(minutes=1)
        assert accounts.token(db, rows[0]) == "tt-token" and rows[0].token_expires_at > datetime.now(UTC)
    renew = [c for c in networks.calls if c[1].endswith("/oauth/token/") and c[2].get("grant_type") == "refresh_token"]
    assert renew and renew[0][2]["refresh_token"] == "tt-refresh"  # noqa: S105 - a fake token
    assert tiktok._pieces(10 * 1024 * 1024) == (10 * 1024 * 1024, 1) and tiktok._pieces(150 * 1024 * 1024) == (tiktok.PIECE, 15)


# --- the owner's character and the agents' tools --------------------------------------------------------------------
def test_the_owner_sets_the_character_and_its_pictures_stay_while_used(veo: FakeVeo, admin: Any) -> None:  # noqa: F811 - the fixture
    up = admin.post("/admin-api/marketing/media/upload", files={"file": ("mila.png", _png(), "image/png")}).json()
    assert admin.put("/admin-api/marketing/character", json={"name": "Mila", "picture_ids": [999999]}).status_code in (400, 422)
    saved = admin.put("/admin-api/marketing/character", json={"name": "Mila", "look": "short curly hair", "picture_ids": [up["id"], up["id"]]}).json()
    assert saved["picture_ids"] == [up["id"]] and admin.get("/admin-api/marketing").json()["character"]["name"] == "Mila"
    assert admin.delete(f"/admin-api/marketing/media/{up['id']}").status_code in (400, 422), "the character's picture stays while it is used"
    with _db() as db:
        brand.save_settings(db, brand.MarketingSettings(monthly_picture_budget_usd=5, monthly_video_budget_usd=10, videos_count=3, videos_per="day"))
    seen: list[int] = []
    veo_generator = studio.generator

    def remember(prompt: str, aspect: str, model: str, *refs: Any) -> studio.Made:
        seen.append(len(refs[0]) if refs else 0)
        return veo_generator(prompt, aspect, model)

    studio.generator = remember
    try:
        ctx = tools.Context(agent=registry.AGENTS["graphic_designer"], run_id=0, db=_db, granted=("create_image", "character", "video_studio"))
        frame = tools._create_image(ctx, prompt="Mila waving in a bright bakery in Bamberg", aspect="9:16", with_character=True)
    finally:
        studio.generator = veo_generator
    assert frame["media_id"] and seen == [1], "the character's picture goes to the picture model"
    assert tools._character(ctx)["name"] == "Mila"
    studio_view = tools._video_studio(ctx)
    assert studio_view["rhythm"] == {"videos": 3, "per": "day", "this_period": 0, "made_this_period": 0, "concepts_waiting": 0, "left": 3}
    assert studio_view["budget"]["left_usd"] == 10 and studio_view["budget"]["price_per_second_usd"] == settings().ai_video_price_per_second_usd


def func_count() -> Any:
    from sqlalchemy import func

    return func.count()


def _tasks() -> Any:
    from app.models import AgentTask

    return AgentTask
