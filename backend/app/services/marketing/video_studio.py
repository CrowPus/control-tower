"""The video studio. The Graphic Designer proposes a concept (video.concept): the
idea, the hook, the scenes with what the camera shows, what the company's character does and says, the sound and
the words on screen, the end card and the post's caption. Nothing is made until the owner approves it.

Then the video is made scene by scene with Google's video model (Veo): each scene gets the character's
pictures as references, so the person stays the same while clothes and places change. A scene takes from
seconds to minutes, so the worker starts the scenes, comes back every few seconds (`marketing.video_step`)
and keeps each scene's operation, never generating (or paying for) a scene twice. When every scene is made,
the scenes are joined (ffmpeg) with the words on screen, the AI label and the brand's end card into one
vertical video in the media library, and the Social Media Manager hears it is ready (`video.ready`) and
proposes when and where it is posted.

What videos cost per month, their quality and how many are made per day or week are the owner's settings
(Admin → Marketing → Brand); without a video budget none is made. The owner deletes a video's file once it
is posted; the concept stays for the record.
"""

from __future__ import annotations

import io
import re
import secrets
import shutil
import subprocess
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageOps
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ...config import settings
from ...core import clock
from ...models import AIUsage, MarketingMedia, MarketingVideo, SocialPost, TeamApproval
from ..team import approvals, events
from ..team.approvals import executor
from . import brand, character, studio

OPERATION = "marketing.video"
# The video model's scene lengths (with reference pictures only 8) and its vertical frame.
SCENE_SECONDS = (4, 6, 8)
WIDTH, HEIGHT = 720, 1280
# Safety rails, not business values: scenes in one video, how long a scene may take, how often the worker looks.
MAX_SCENES = 5
SCENE_TIMEOUT = timedelta(minutes=25)
STEP_SECONDS = 15
END_CARD_SECONDS = 2.5
NETWORKS = ("tiktok", "instagram", "facebook", "youtube", "linkedin")

VIDEO_RULES = (
    "Cinematic, natural and credible, real-looking places as the scene describes, adults only. No text, letters, captions, subtitles, logos, brand names "
    "or watermarks anywhere in the picture (they are added later). No real, famous or identifiable persons. No flags, symbols or uniforms of authorities."
)


def video_rules(kit: brand.BrandKit) -> str:
    what = f" ({kit.tagline})" if kit.tagline else ""
    return f"A vertical 9:16 short video for TikTok, Instagram Reels and Facebook Reels by {kit.name}{what}. {VIDEO_RULES}"
# What the scenes must not show, said in the prompt itself: Veo refuses a separate negative prompt together with reference
# pictures ("Negative prompt is not supported in your use case", seen live 2026-10-09).
AVOID = "Avoid: text, captions, subtitles, title cards, intertitles, letters, logos, watermarks, brand names, distorted faces, extra fingers, children."


# How the name is said, when the owner gave it (brand kit): the video model is told so whenever the name is spoken.
BRAND_SAY = 'The name "{name}" is said "{pronunciation}", the same in every language.'
# A silent character (the owner's choice): the comedy is in faces, bodies and timing; the sound is music and effects only.
# A silent character (the owner's choice): the comedy is in faces, bodies and timing; the sound is music and effects only. Not
# "like an old silent film": told that, the video model added title cards with made-up words ("Heldotale", seen 2026-10-09).
SILENT = ("{name} never speaks: no words, no talking, no mouthing words. The comedy is told with big, clear facial expressions, body language "
          "and comic timing, filmed as a modern, natural, colour video — no title cards, no intertitles, no words on screen at any moment. "
          "Sound: only music and sound effects — no voices, no narration, no sung words.")
SIMPLE = ("Keep the motion simple and natural: one action, hands calm and in view only when needed, the camera steady. Everything in the first "
          "frame stays exactly as it is for the whole scene: glasses stay on the face, every object stays where it is (cups, notebooks, phones, "
          "plants), nothing appears, disappears or changes. No readable text on any screen, paper or badge.")
# Each scene after the first continues from the last picture of the scene before (2026-10-09: scenes made each from their
# own storyboard picture lost glasses, a mug and a notebook at every cut).
CONTINUE = "The first frame is the last moment of the scene before: continue from exactly this picture — the same person, clothes, glasses, objects, light and place."


def spoken(text: str, name: str = "") -> str:
    """The words as the video model should say them: the company's name always written the owner's way."""
    return re.sub(rf"(?i)\b{re.escape(name)}\b", name, text) if name else text


class VideoRefused(ValueError):
    """The video cannot be made; the reason is shown (to the agent or the owner)."""


# --- the video model (tests replace these) -----------------------------------------------------------------
@dataclass
class Poll:
    done: bool
    uri: str = ""
    error: str = ""


def _client() -> Any:
    from google import genai

    return genai.Client(api_key=settings().gemini_api_key)


def _start(prompt: str, model: str, references: Sequence[tuple[bytes, str]], seconds: int, first_frame: tuple[bytes, str] | None = None) -> str:
    """Start one scene: from its approved first frame (the storyboard picture — the same person, clothes and place in
    every scene), or with the character's pictures as references when the scene has no frame."""
    from google.genai import types

    config = types.GenerateVideosConfig(aspect_ratio="9:16", duration_seconds=seconds, resolution="720p", person_generation="allow_adult", number_of_videos=1,
                                        reference_images=None if first_frame else
                                        [types.VideoGenerationReferenceImage(image=types.Image(image_bytes=data, mime_type=mime), reference_type="asset")
                                         for data, mime in references] or None)
    client = _client()  # kept for the call: a client that is let go closes its connection
    image = types.Image(image_bytes=first_frame[0], mime_type=first_frame[1]) if first_frame else None
    operation = client.models.generate_videos(model=model, prompt=prompt, image=image, config=config)
    return str(operation.name)


def _poll(name: str) -> Poll:
    from google.genai import types

    client = _client()
    operation = client.operations.get(types.GenerateVideosOperation.model_validate({"name": name}))
    if not operation.done:
        return Poll(done=False)
    if operation.error:
        return Poll(done=True, error=str(operation.error.get("message") if isinstance(operation.error, dict) else operation.error)[:300])
    response = operation.response
    videos = (response.generated_videos if response else None) or []
    if not videos or not videos[0].video or not videos[0].video.uri:
        reasons = "; ".join((response.rai_media_filtered_reasons or []) if response else []) or "the model made no video (it may have declined the scene)"
        return Poll(done=True, error=f"Not made: {reasons}"[:300])
    return Poll(done=True, uri=str(videos[0].video.uri))


def _download(uri: str) -> bytes:
    import httpx

    response = httpx.get(uri, headers={"x-goog-api-key": settings().gemini_api_key}, follow_redirects=True, timeout=300)
    response.raise_for_status()
    return response.content


starter: Callable[..., str] = _start
poller: Callable[[str], Poll] = _poll
downloader: Callable[[str], bytes] = _download


# --- money and rhythm ----------------------------------------------------------------------------------------
def model_and_price(session: Session) -> tuple[str, float]:
    best = brand.settings_of(session).video_quality == "best"
    cfg = settings()
    return (cfg.gemini_video_model_best, cfg.ai_video_price_per_second_best_usd) if best else (cfg.gemini_video_model, cfg.ai_video_price_per_second_usd)


def spent_this_month(session: Session) -> float:
    start = datetime.now(UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return float(session.execute(select(func.coalesce(func.sum(AIUsage.estimated_cost_usd), 0.0))
                                 .where(AIUsage.operation == OPERATION, AIUsage.created_at >= start)).scalar() or 0.0)


def budget(session: Session) -> dict[str, Any]:
    cap = brand.settings_of(session).monthly_video_budget_usd
    spent = spent_this_month(session)
    _model, per_second = model_and_price(session)
    return {"monthly_budget_usd": cap, "spent_this_month_usd": round(spent, 2), "left_usd": None if cap is None else round(max(cap - spent, 0), 2),
            "price_per_second_usd": per_second}


def _period_start(per: str, now: datetime) -> datetime:
    local = now.astimezone(clock.zone())
    day = local.replace(hour=0, minute=0, second=0, microsecond=0)
    return (day if per == "day" else day - timedelta(days=local.weekday())).astimezone(UTC)


def rhythm(session: Session, *, leave_out: int | None = None) -> dict[str, Any]:
    """The owner's videos per day or week, and how many this period already has (made, being made, or waiting for approval)."""
    wanted = brand.settings_of(session)
    start = _period_start(wanted.videos_per, datetime.now(UTC))
    made = int(session.execute(select(func.count()).select_from(MarketingVideo).where(MarketingVideo.created_at >= start, MarketingVideo.status != "failed")).scalar() or 0)
    waiting_stmt = select(func.count()).select_from(TeamApproval).where(TeamApproval.action_type == "video.concept", TeamApproval.status.in_(("pending", "approved")))
    if leave_out is not None:
        waiting_stmt = waiting_stmt.where(TeamApproval.id != leave_out)
    waiting = int(session.execute(waiting_stmt).scalar() or 0)
    count = wanted.videos_count
    return {"videos": count, "per": wanted.videos_per, "this_period": made + waiting, "made_this_period": made, "concepts_waiting": waiting,
            "left": None if count is None else max(count - made - waiting, 0)}


# --- the concept -----------------------------------------------------------------------------------------------
def scenes_of(concept: dict[str, Any]) -> list[dict[str, Any]]:
    raw = concept.get("scenes")
    return [s for s in raw if isinstance(s, dict)] if isinstance(raw, list) else []


def seconds_of(concept: dict[str, Any]) -> int:
    return sum(int(s.get("seconds") or 8) for s in scenes_of(concept))


def check(session: Session, payload: dict[str, Any], *, leave_out: int | None = None) -> list[str]:
    """What is wrong with a proposed concept (empty: it can be made)."""
    problems: list[str] = []
    if not str(payload.get("title") or "").strip():
        problems.append("Give the concept a title.")
    if len(str(payload.get("idea") or "").strip()) < 30:
        problems.append("Describe the idea in two or three sentences (idea).")
    if not str(payload.get("hook") or "").strip():
        problems.append("Say what happens in the first two seconds (hook).")
    scenes = scenes_of(payload)
    if not 1 <= len(scenes) <= MAX_SCENES:
        problems.append(f"A video has 1 to {MAX_SCENES} scenes.")
    uses = bool(payload.get("uses_character", True))
    if len(str(payload.get("setting") or "").strip()) < 10:
        problems.append("Say where the video plays (setting): one place, kept in every scene.")
    if uses and len(str(payload.get("outfit") or "").strip()) < 10:
        problems.append("Say what the character wears in this video (outfit): the same in every scene.")
    frames: list[int] = []
    silent = not character.get(session).speaks
    for number, scene in enumerate(scenes, start=1):
        seconds = int(scene.get("seconds") or 8) if str(scene.get("seconds") or "8").isdigit() else 0
        if seconds not in SCENE_SECONDS:
            problems.append(f"Scene {number}: seconds is 4, 6 or 8.")
        elif uses and seconds != 8:
            problems.append(f"Scene {number}: scenes with the character are 8 seconds long (the video model needs that with reference pictures).")
        if len(str(scene.get("shot") or "").strip()) < 20:
            problems.append(f"Scene {number}: describe the shot (what the camera shows, where, the light) in a sentence or two.")
        if uses and silent and str(scene.get("dialogue") or "").strip():
            problems.append(f"Scene {number}: the character does not speak (the owner's choice) — tell it with action, facial expression, music and sound effects; "
                            "the message goes on the end card and in the caption.")
        if len(str(scene.get("dialogue") or "").split()) > int(2.5 * max(seconds, 4)):
            problems.append(f"Scene {number}: too many words to say in {seconds} seconds (at most {int(2.5 * max(seconds, 4))}).")
        frame = scene.get("start_frame")
        if uses and not str(frame or "").isdigit():
            problems.append(f"Scene {number}: make its first frame with storyboard_frame and give its media_id as start_frame.")
        elif str(frame or "").isdigit():
            frames.append(int(str(frame)))
    if frames:
        found = {m.id for m in session.execute(select(MarketingMedia).where(MarketingMedia.id.in_(frames), MarketingMedia.source == "storyboard")).scalars()}
        if any(f not in found for f in frames):
            problems.append("A start_frame is not a storyboard picture (make it with storyboard_frame).")
    raw_end = payload.get("end_card")
    end: dict[str, Any] = raw_end if isinstance(raw_end, dict) else {}
    if len(str(end.get("headline") or "")) > 60 or len(str(end.get("subline") or "")) > 90:
        problems.append("The end card's headline has at most 60 characters, its line under it 90.")
    networks = payload.get("networks") or []
    if not isinstance(networks, list) or any(n not in NETWORKS for n in networks):
        problems.append(f"networks are some of {', '.join(NETWORKS)}.")
    if uses:
        figure = character.get(session)
        if not figure.ready or not character.pictures(session, figure):
            problems.append("The company's character is not set up yet (Admin → Marketing → Character: its name and pictures); the owner does that first.")
    wanted = brand.settings_of(session)
    if wanted.monthly_video_budget_usd is None:
        problems.append("The owner has not set a monthly video budget yet (Admin → Marketing → Brand), so no video is made.")
    else:
        _model, per_second = model_and_price(session)
        cost = seconds_of(payload) * per_second
        left = wanted.monthly_video_budget_usd - spent_this_month(session)
        if cost > left + 1e-9:
            problems.append(f"This video would cost about ${cost:.2f}; this month's video budget has ${max(left, 0):.2f} left.")
    pace = rhythm(session, leave_out=leave_out)
    if pace["videos"] is None:
        problems.append("The owner has not said how many videos to make per day or week (Admin → Marketing → Brand).")
    elif pace["left"] == 0:
        problems.append(f"This {pace['per']} already has its {pace['videos']} video(s) (made or waiting for approval).")
    return problems


approvals.CHECKS["video.concept"] = lambda session, payload: check(session, payload)
approvals.TARGET["video.concept"] = "title"


@executor("video.concept")
def _approved(session: Session, approval: TeamApproval, payload: dict[str, Any]) -> dict[str, Any]:
    problems = check(session, payload, leave_out=approval.id)
    if problems:
        raise ValueError(" ".join(problems))
    model, _price = model_and_price(session)
    row = MarketingVideo(title=str(payload.get("title"))[:200], concept=payload, status="making", model=model, uses_character=bool(payload.get("uses_character", True)),
                         progress={"scenes": [{"state": "start"} for _ in scenes_of(payload)]}, approval_id=approval.id, agent=approval.agent,
                         created_at=datetime.now(UTC))
    session.add(row)
    session.flush()
    queue_step(session, row, now=True)
    return {"video": row.id, "scenes": len(scenes_of(payload)), "seconds": seconds_of(payload)}


def queue_step(session: Session, row: MarketingVideo, *, now: bool = False, next_look: bool = False) -> None:
    """Have the worker look at the video. Each look has its own key (the look that queues the next is still running), so
    a lost look is queued again under the same key without ever doubling."""
    from ...workers import queue as q

    look = int((row.progress or {}).get("look") or 0) + (1 if next_look else 0)
    if next_look:
        row.progress = {**(row.progress or {}), "look": look}
    when = datetime.now(UTC) + (timedelta(0) if now else timedelta(seconds=STEP_SECONDS))
    q.enqueue(session, "marketing.video_step", {"video_id": row.id}, queue="default", priority=3, run_at=when, max_attempts=3,
              dedupe_key=f"marketing.video:{row.id}:{look}")


# --- making it --------------------------------------------------------------------------------------------------
def folder(*parts: str) -> Path:
    path = Path(settings().marketing_dir).joinpath(*parts)
    path.mkdir(parents=True, exist_ok=True)
    return path


def scene_prompt(concept: dict[str, Any], index: int, figure: character.Character | None, style: str, *, from_frame: bool = False, continues: bool = False,
                 fixes: Sequence[str] = (), kit: brand.BrandKit | None = None) -> str:
    """What the video model is told for one scene: the person as it looks and sounds, the outfit and place kept in every scene,
    one simple action, exactly the words to say — and nothing it could read as more to say (the owner's rules and catchphrases
    are the agent's, not the video model's: given to it, the model spoke a catchphrase that was not in the script)."""
    scenes = scenes_of(concept)
    scene = scenes[index]
    name = figure.name if figure is not None else "The person"
    kit = kit or brand.BrandKit(name=settings().app_name)
    lines = [video_rules(kit)]
    if figure is not None:
        if figure.look:
            lines.append(f"{name}: {figure.look}")
        if figure.speaks and figure.voice:
            lines.append(f"{name}'s voice: {figure.voice}")
        lines.append(CONTINUE if continues else f"The first frame shows {name}: keep exactly this person, these clothes and this place." if from_frame
                     else f"The reference pictures show {name}: show exactly this person.")
    if continues and figure is None:
        lines.append(CONTINUE)
    if concept.get("outfit"):
        lines.append(f"{name} wears: {concept['outfit']} — the same in every scene.")
    if style:
        lines.append(f"Visual style: {style}")
    if concept.get("setting"):
        lines.append(f"Place (the same in every scene): {concept['setting']}")
    lines.append(f"Scene {index + 1} of {len(scenes)}. Shot: {scene.get('shot', '')}")
    if scene.get("action"):
        lines.append(f"What happens: {scene['action']}")
    if scene.get("expression"):
        lines.append(f"{name}'s face: {scene['expression']}")
    words = spoken(str(scene.get("dialogue") or ""), kit.name).strip()
    if figure is not None and not figure.speaks:
        lines.append(SILENT.format(name=name))
    elif words:
        lines.append(f'{name} says in English, exactly these words and nothing else: "{words}"')
        if kit.pronunciation and kit.name and kit.name.lower() in words.lower():
            lines.append(BRAND_SAY.format(name=kit.name, pronunciation=kit.pronunciation))
    else:
        lines.append(f"{name} does not speak in this scene.")
    if scene.get("sound"):
        lines.append(f"Sound: {scene['sound']}")
    lines.append(SIMPLE)
    if fixes:
        lines.append("Last time this scene went wrong; make sure none of this happens: " + " ".join(f"{f.rstrip('.')}." for f in fixes))
    lines.append(AVOID)
    return "\n".join(lines)


def step(session: Session, row: MarketingVideo) -> bool:
    """One look at the video being made: start scenes, collect the made ones, join them when all are made.
    True when the worker should look again."""
    if row.status != "making":
        return False
    concept = row.concept or {}
    scenes = scenes_of(concept)
    items = [dict(x) for x in (row.progress or {}).get("scenes") or [{"state": "start"} for _ in scenes]]
    figure = character.get(session) if row.uses_character else None
    references = [(m.data or b"", m.mime_type) for m in character.pictures(session, figure)] if figure else []
    the_kit = brand.kit(session)
    style = the_kit.image_style
    wanted = brand.settings_of(session)
    _model, per_second = model_and_price(session)
    now = datetime.now(UTC)
    # Scenes under way are paid when they are made: they count against the budget already.
    under_way = in_flight(session, leave_out=row.id) + sum(int(scenes[i].get("seconds") or 8) * per_second for i, x in enumerate(items) if x["state"] == "wait")
    for index, item in enumerate(items):
        seconds = int(scenes[index].get("seconds") or 8)
        if item["state"] == "start":
            # One after another: a scene starts from the last picture of the scene before, so the person, the clothes and
            # every object carry over the cut. The first scene starts from its approved storyboard frame.
            before = items[index - 1] if index else None
            if before is not None and before["state"] != "done":
                if before["state"] == "failed":
                    item.update(state="failed", error=f"Waits for scene {index}, which was not made.")
                continue
            cap = wanted.monthly_video_budget_usd
            if cap is None or spent_this_month(session) + under_way + seconds * per_second > cap + 1e-9:
                item.update(state="failed", error="This month's video budget is used up; the owner can raise it in Admin → Marketing → Brand.")
                continue
            if row.uses_character and not references and not str(scenes[index].get("start_frame") or "").isdigit():
                item.update(state="failed", error="The company's character has no pictures any more (Admin → Marketing → Character).")
                continue
            frame_id = str(scenes[index].get("start_frame") or "")
            frame_row = session.get(MarketingMedia, int(frame_id)) if frame_id.isdigit() else None
            frame = (frame_row.data, frame_row.mime_type) if frame_row is not None and frame_row.data else None
            continues = False
            if before is not None:
                last = last_frame(folder("work", str(row.id)) / str(before["file"]))
                if last:
                    frame, continues = (last, "image/png"), True
            try:
                prompt = scene_prompt(concept, index, figure, style, from_frame=frame is not None, continues=continues, fixes=item.get("fixes") or [], kit=the_kit)
                operation = starter(prompt, row.model, references, seconds, frame) if frame else starter(prompt, row.model, references, seconds)
                item.update(state="wait", operation=operation, started_at=now.isoformat(), error="")
                under_way += seconds * per_second
            except Exception as exc:  # noqa: BLE001 - shown on the video; a busy model is tried again on the next look
                tries = int(item.get("tries") or 0) + 1
                busy = "429" in str(exc) or "RESOURCE_EXHAUSTED" in str(exc) or "503" in str(exc)
                if busy and tries < 20:
                    item.update(tries=tries, error="The video model is busy; trying again.")
                else:
                    item.update(state="failed", error=f"The video model refused the scene ({exc.__class__.__name__}: {str(exc)[:200]}).")
        elif item["state"] == "wait":
            try:
                polled = poller(str(item["operation"]))
            except Exception as exc:  # noqa: BLE001 - asked again on the next look
                item["error"] = f"Could not ask the video model ({exc.__class__.__name__}); asking again."
                polled = Poll(done=False)
            if not polled.done:
                if now - datetime.fromisoformat(str(item["started_at"])) > SCENE_TIMEOUT:
                    item.update(state="failed", error="The video model took too long for this scene.")
                continue
            if polled.error or not polled.uri:
                # A scene the model declined is not charged.
                _usage(session, row.model, 0.0, success=False, error="declined")
                item.update(state="failed", error=polled.error or "No video came back.")
                continue
            try:
                data = downloader(polled.uri)
            except Exception as exc:  # noqa: BLE001 - the made scene stays on the model's server for two days; fetched again on the next look
                item["error"] = f"Could not fetch the made scene ({exc.__class__.__name__}); trying again."
                continue
            name = f"scene-{index + 1}.mp4"
            (folder("work", str(row.id)) / name).write_bytes(data)
            cost = round(seconds * per_second, 4)
            _usage(session, row.model, cost, success=True)
            row.cost_usd = round((row.cost_usd or 0) + cost, 4)
            item.update(state="done", file=name, cost_usd=cost, error="")
    row.progress = {**(row.progress or {}), "scenes": items}
    if any(i["state"] in ("start", "wait") for i in items):
        return True
    failed = [f"Scene {n}: {i.get('error')}" for n, i in enumerate(items, start=1) if i["state"] == "failed"]
    if failed:
        row.status, row.error = "failed", " ".join(failed)[:500]
        return False
    try:
        media = finish(session, row)
    except VideoRefused as exc:
        row.status, row.error = "failed", str(exc)[:500]
        return False
    row.status, row.media_id, row.made_at, row.error = "ready", media.id, datetime.now(UTC), ""
    row.progress = {**(row.progress or {}), "review": {"state": "waiting"}}
    # The made scenes are kept with the video (a scene can be made again after the check); they go when the video is deleted.
    replaced = (row.progress or {}).pop("replaces", None)
    if replaced:
        old_media = session.get(MarketingMedia, int(replaced))
        if old_media is not None and not posts_using(session, old_media.id):
            remove_file(old_media)
            session.delete(old_media)
    # Watched for AI mistakes and wrong words before the Social Media Manager hears of it (its own task: the check takes a while).
    from ...workers import queue as q

    q.enqueue(session, "marketing.video_review", {"video_id": row.id}, queue="default", priority=3, max_attempts=2, dedupe_key=f"marketing.video_review:{row.id}")
    return False


# --- the check after making -------------------------------------------------------------------------------------------
REVIEW_OPERATION = "marketing.video_review"
REVIEW_FPS = 3
# Safety rail: the largest video sent inline to the model (its limit is about 20 MB per request).
REVIEW_MAX_BYTES = 18 * 1024 * 1024
REVIEW_PROMPT = (
    "You check a short AI-made video for {company} before it is posted on TikTok, Instagram and Facebook. "
    "Watch and listen to all of it. Answer in JSON only:\n"
    '{"said": "every spoken word, as spoken (write numbers as pronounced)", '
    '"brand_in_english": true or false or null (is the name {company} said as "{pronunciation}"? null when it is not said or no way is given), '
    '"mistakes": [{"at": "m:ss", "what": "one visible or audible AI mistake: hands or fingers, faces or teeth, objects that morph, appear or vanish, '
    'clothes, hair, glasses or place changing between scenes, garbled text, wrong or extra words compared with the script", "severity": "high|low"}], '
    '"false_claims": ["anything said or shown about {company} that the facts below do not support"], '
    '"verdict": "post" or "fix", "summary": "one or two sentences for the owner"}\n'
    "Claims: go through every sentence that is said, shown or in the script, one by one; any statement about what {company} does, offers, costs or "
    "leaves out that the facts do not say word for word in meaning is a false claim (e.g. 'guaranteed visa' when the facts say "
    "nobody can promise a visa).\n"
    "High severity: the viewer notices at once (a face changes, a hand melts, an object morphs, the person or clothes change between scenes, a false claim, "
    "the name said wrongly, or any voice when the script says the character is silent). Say fix when anything is high.\n\n"
)


def review_prompt() -> str:
    from ...db import transaction

    with transaction() as session:
        kit = brand.kit(session)
    return REVIEW_PROMPT.replace("{company}", kit.name).replace("{pronunciation}", kit.pronunciation or "the owner's way")


def _review(video: bytes, script: str, facts: str) -> tuple[dict[str, Any], str, int, int]:
    import json

    from google.genai import types

    from ...ai.client import ModelConfig

    # The stronger model: the light one missed melting hands, a morphing laptop and words that were not in the script (seen 2026-10-09).
    model = ModelConfig.resolve("reasoning")
    client = _client()
    # Three frames a second (the default one misses a hand melting for half a second).
    clip = types.Part(inline_data=types.Blob(data=video, mime_type="video/mp4"), video_metadata=types.VideoMetadata(fps=REVIEW_FPS))
    response = client.models.generate_content(model=model, contents=[clip, f"{review_prompt()}The script:\n{script}\n\nWhat may be said (facts):\n{facts}"],
                                              config=types.GenerateContentConfig(response_mime_type="application/json", temperature=0.1))
    usage = response.usage_metadata
    result = json.loads(response.text or "{}")
    return (result if isinstance(result, dict) else {}), model, int(getattr(usage, "prompt_token_count", 0) or 0), int(getattr(usage, "candidates_token_count", 0) or 0)


# Tests replace this: (video bytes, script, facts) -> (result, model, input tokens, output tokens).
reviewer: Callable[[bytes, str, str], tuple[dict[str, Any], str, int, int]] = _review


def script_of(concept: dict[str, Any], *, silent: bool = False) -> str:
    lines = ["SILENT: the character never speaks; only music and sound effects. Any voice or words is a high mistake."] if silent else []
    for number, scene in enumerate(scenes_of(concept), start=1):
        said = "" if silent else f" Says: \"{scene.get('dialogue', '')}\""
        lines.append(f"Scene {number} ({scene.get('seconds') or 8} s): {scene.get('shot', '')} {scene.get('action', '')} {scene.get('expression', '')}{said}".rstrip())
    raw_end = concept.get("end_card")
    end: dict[str, Any] = raw_end if isinstance(raw_end, dict) else {}
    if end:
        lines.append(f"End card: {end.get('headline', '')} — {end.get('subline', '')}")
    return "\n".join(lines)


def facts_of(session: Session) -> str:
    kit = brand.kit(session)
    return "\n".join([f"- {kit.name}: {kit.tagline}" if kit.tagline else f"- {kit.name}", *(f"- {f}" for f in kit.facts_for_customers)])


def review_now(row_id: int, db: Callable[[], Any]) -> dict[str, Any]:
    """Watch the made video once (outside any transaction), keep what was found, and tell the Social Media Manager."""
    with db() as session:
        row = session.get(MarketingVideo, row_id)
        media = session.get(MarketingMedia, row.media_id) if row is not None and row.media_id else None
        path = file_of(media) if media is not None else None
        if row is None or row.status != "ready" or path is None:
            return {"skipped": "no video"}
        silent = row.uses_character and not character.get(session).speaks
        concept, script, facts = dict(row.concept or {}), script_of(row.concept or {}, silent=silent), facts_of(session)
    found: dict[str, Any]
    started = time.monotonic()
    model, tokens_in, tokens_out = "", 0, 0
    if path.stat().st_size > REVIEW_MAX_BYTES:
        found = {"state": "skipped", "summary": "Too large to check automatically; watch it before posting."}
    else:
        try:
            result, model, tokens_in, tokens_out = reviewer(path.read_bytes(), script, facts)
            mistakes = [m for m in result.get("mistakes") or [] if isinstance(m, dict)][:20]
            claims = [str(c) for c in result.get("false_claims") or []][:10]
            fix = (result.get("verdict") == "fix" or any(m.get("severity") == "high" for m in mistakes) or bool(claims)
                   or result.get("brand_in_english") is False)
            found = {"state": "done", "verdict": "fix" if fix else "post", "summary": str(result.get("summary") or "")[:600], "said": str(result.get("said") or "")[:2000],
                     "brand_in_english": result.get("brand_in_english"), "mistakes": mistakes, "false_claims": claims}
        except Exception as exc:  # noqa: BLE001 - an unchecked video is said to be so, never held back by the check failing
            found = {"state": "failed", "summary": f"The automatic check failed ({exc.__class__.__name__}); watch it before posting."}
    with db() as session:
        row = session.get(MarketingVideo, row_id)
        if row is None:
            return {"skipped": "gone"}
        row.progress = {**(row.progress or {}), "review": found}
        if model:
            from ...ai.client import record_usage

            record_usage(model=model, operation=REVIEW_OPERATION, agent_type="team", input_tokens=tokens_in, output_tokens=tokens_out,
                         latency_ms=int((time.monotonic() - started) * 1000), success=True, session=session)
        media = session.get(MarketingMedia, row.media_id) if row.media_id else None
        events.emit(session, "video.ready", "graphic_designer", {
            "to": "content_community", "video_id": row.id, "media_id": row.media_id, "title": row.title, "seconds": media.seconds if media else 0,
            "caption": str(concept.get("caption") or "")[:2200], "hashtags": concept.get("hashtags") or [], "networks": concept.get("networks") or [],
            "review": {k: found.get(k) for k in ("state", "verdict", "summary", "false_claims")},
            "note": ("A finished video: propose a social.post of kind video with this media_id, at a time that keeps the owner's videos per day or week. "
                     "If its review says fix, do not propose it: tell the owner what the review found."),
        })
    return {"video": row_id, "review": found.get("verdict") or found.get("state")}


def last_frame(path: Path) -> bytes | None:
    """The last picture of a made scene (PNG), where the next scene starts."""
    if not path.exists():
        return None
    done = subprocess.run([ffmpeg(), "-hide_banner", "-nostdin", "-loglevel", "error", "-sseof", "-0.25", "-i", str(path), "-frames:v", "1",  # noqa: S603 - our own arguments
                           "-f", "image2pipe", "-vcodec", "png", "-"], capture_output=True, timeout=120, check=False)
    return done.stdout if done.returncode == 0 and done.stdout else None


def _seconds_at(at: str) -> float | None:
    try:
        minutes, seconds = str(at).strip().split(":")
        return int(minutes) * 60 + float(seconds)
    except ValueError:
        return None


def mistakes_by_scene(concept: dict[str, Any], review: dict[str, Any]) -> dict[int, list[str]]:
    """The check's mistakes, by the scene (0-based) they happen in; one at the very start of a scene belongs to it (a cut)."""
    starts, total = [], 0.0
    for scene in scenes_of(concept):
        starts.append(total)
        total += int(scene.get("seconds") or 8)
    out: dict[int, list[str]] = {}
    for m in review.get("mistakes") or []:
        at = _seconds_at(str(m.get("at") or "")) if isinstance(m, dict) else None
        if at is None or at >= total:
            continue
        index = max(i for i, start in enumerate(starts) if start <= at + 0.25)
        out.setdefault(index, []).append(str(m.get("what") or "")[:300])
    return out


def remake_from(concept: dict[str, Any], review: dict[str, Any]) -> int | None:
    """The first scene to make again (1-based) after the check found mistakes, or None."""
    found = mistakes_by_scene(concept, review)
    return min(found) + 1 if found else None


def remake(session: Session, row: MarketingVideo, from_scene: int) -> float:
    """The owner makes a finished video's scenes again from one scene on (each continues from the one before), with what the check
    found in each told to the video model. The scenes before are kept; the old video goes when the new one is made (unless posted).
    Returns what the new scenes will cost."""
    if row.status != "ready":
        raise VideoRefused("Only a finished video can be made again.")
    concept = row.concept or {}
    scenes = scenes_of(concept)
    if not 1 <= from_scene <= len(scenes):
        raise VideoRefused(f"Choose a scene from 1 to {len(scenes)}.")
    _model, per_second = model_and_price(session)
    cost = sum(int(s.get("seconds") or 8) for s in scenes[from_scene - 1:]) * per_second
    cap = brand.settings_of(session).monthly_video_budget_usd
    if cap is None or spent_this_month(session) + in_flight(session) + cost > cap + 1e-9:
        raise VideoRefused(f"Making {len(scenes) - from_scene + 1} scene(s) again costs about ${cost:.2f}; this month's video budget does not cover it (Admin → Marketing → Brand).")
    media = session.get(MarketingMedia, row.media_id) if row.media_id else None
    work = folder("work", str(row.id))
    items = [dict(x) for x in (row.progress or {}).get("scenes") or [{"state": "done", "file": f"scene-{n + 1}.mp4"} for n in range(len(scenes))]]
    # Scenes kept: from the work folder, or cut out of the finished video (videos made before the scenes were kept).
    start = 0.0
    for index in range(from_scene - 1):
        seconds = int(scenes[index].get("seconds") or 8)
        path = work / f"scene-{index + 1}.mp4"
        if not path.exists():
            source = file_of(media) if media is not None else None
            if source is None:
                raise VideoRefused("The finished video's file is gone; make the video again from scene 1.")
            _run(["-y", "-ss", f"{start:.3f}", "-t", str(seconds), "-i", str(source), "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-c:a", "aac", str(path)])
        items[index] = {"state": "done", "file": path.name, "cost_usd": items[index].get("cost_usd", 0)}
        start += seconds
    found = mistakes_by_scene(concept, (row.progress or {}).get("review") or {})
    for index in range(from_scene - 1, len(scenes)):
        items[index] = {"state": "start", "fixes": found.get(index, [])}
    row.progress = {**(row.progress or {}), "scenes": items, "replaces": row.media_id, "review": {}, "remade_at": datetime.now(UTC).isoformat()}
    # Only what the admin's database role may change here (db_roles: status, progress, error, media_id); made_at is the worker's.
    row.status, row.error, row.media_id = "making", "", None
    queue_step(session, row, now=True, next_look=True)
    return round(cost, 2)


def in_flight(session: Session, *, leave_out: int | None = None) -> float:
    """What the scenes being made right now (in other videos) will cost when they are made."""
    _model, per_second = model_and_price(session)
    total = 0.0
    for other in session.execute(select(MarketingVideo).where(MarketingVideo.status == "making")).scalars():
        if other.id == leave_out:
            continue
        scenes = scenes_of(other.concept or {})
        for index, item in enumerate((other.progress or {}).get("scenes") or []):
            if item.get("state") == "wait" and index < len(scenes):
                total += int(scenes[index].get("seconds") or 8) * per_second
    return total


def again(session: Session, row: MarketingVideo) -> None:
    """The owner tries a failed video again: scenes already made are kept, the others are made again."""
    if row.status != "failed":
        raise VideoRefused("Only a video that failed can be tried again.")
    items = [dict(x) for x in (row.progress or {}).get("scenes") or []]
    for item in items:
        if item.get("state") != "done":
            item.clear()
            item["state"] = "start"
    row.progress, row.status, row.error = {**(row.progress or {}), "scenes": items, "restarted_at": datetime.now(UTC).isoformat()}, "making", ""
    queue_step(session, row, now=True, next_look=True)


def _usage(session: Session, model: str, cost: float, *, success: bool, error: str = "") -> None:
    session.add(AIUsage(agent_type="team", model=model, operation=OPERATION, input_tokens=0, output_tokens=0, estimated_cost_usd=cost,
                        latency_ms=0, success=success, error_class=error[:80], created_at=datetime.now(UTC)))


# --- joining the scenes -------------------------------------------------------------------------------------------
def ffmpeg() -> str:
    import imageio_ffmpeg  # type: ignore[import-untyped]

    return str(imageio_ffmpeg.get_ffmpeg_exe())


def _run(args: list[str], timeout: int = 600) -> str:
    done = subprocess.run([ffmpeg(), "-hide_banner", "-nostdin", *args], capture_output=True, text=True, timeout=timeout, check=False)  # noqa: S603 - our own arguments
    if done.returncode != 0:
        raise VideoRefused(f"Joining the scenes failed: {done.stderr.strip().splitlines()[-1][:200] if done.stderr.strip() else done.returncode}")
    return done.stderr


def _has_audio(path: Path) -> bool:
    probe = subprocess.run([ffmpeg(), "-hide_banner", "-nostdin", "-i", str(path)], capture_output=True, text=True, timeout=60, check=False)  # noqa: S603
    return "Audio:" in probe.stderr


def _png(image: Image.Image, path: Path) -> Path:
    image.save(path, format="PNG")
    return path


def label_layer(label: str) -> Image.Image:
    layer = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    if not label:
        return layer
    draw = ImageDraw.Draw(layer)
    font = studio._font(24)
    width = int(draw.textlength(label, font=font))
    x, y = 28, int(HEIGHT * 0.085)
    draw.rounded_rectangle([x, y, x + width + 24, y + 38], radius=10, fill=(0, 0, 0, 140))
    draw.text((x + 12, y + 5), label, font=font, fill=(255, 255, 255, 235))
    return layer


def end_card(kit: brand.BrandKit, logo: bytes | None, headline: str, subline: str) -> Image.Image:
    card = Image.new("RGB", (WIDTH, HEIGHT), studio._hex(kit.primary_color))
    draw = ImageDraw.Draw(card, "RGBA")
    y = int(HEIGHT * 0.30)
    if logo:
        mark = ImageOps.exif_transpose(Image.open(io.BytesIO(logo))).convert("RGBA")
        mark.thumbnail((420, 160))
        plate = Image.new("RGBA", (mark.width + 56, mark.height + 40), (255, 255, 255, 240))
        px = (WIDTH - plate.width) // 2
        card.paste(plate, (px, y), plate)
        card.paste(mark, (px + 28, y + 20), mark)
        y += plate.height + 60
    else:
        big = studio._font(64, bold=True)
        draw.text(((WIDTH - int(draw.textlength(kit.name, font=big))) // 2, y), kit.name, font=big, fill=studio._hex(kit.text_color))
        y += 120
    for text, size, bold in ((headline, 50, True), (subline, 32, False)):
        if not text:
            continue
        font = studio._font(size, bold=bold)
        for line in studio._wrap(draw, text, font, WIDTH - 120):
            draw.text(((WIDTH - int(draw.textlength(line, font=font))) // 2, y), line, font=font, fill=studio._hex(kit.text_color))
            y += int(size * 1.35)
        y += 24
    draw.rectangle([(WIDTH - 120) // 2, y + 10, (WIDTH + 120) // 2, y + 18], fill=studio._hex(kit.accent_color))
    return card


def finish(session: Session, row: MarketingVideo) -> MarketingMedia:
    """Join the made scenes with the words on screen, the AI label and the end card; keep the video in the media library."""
    concept = row.concept or {}
    scenes = scenes_of(concept)
    work = folder("work", str(row.id))
    files = [work / str(item["file"]) for item in (row.progress or {}).get("scenes") or []]
    if len(files) != len(scenes) or not all(f.exists() for f in files):
        raise VideoRefused("A made scene is missing; try the video again.")
    kit = brand.kit(session)
    logo_row = session.get(MarketingMedia, kit.logo_media_id) if kit.logo_media_id else None
    logo = logo_row.data if logo_row is not None and logo_row.kind == "image" else None
    raw_end = concept.get("end_card")
    end: dict[str, Any] = raw_end if isinstance(raw_end, dict) else {}
    end_png = _png(end_card(kit, logo, str(end.get("headline") or kit.tagline or ""), str(end.get("subline") or "")), work / "end.png")
    label_png = _png(label_layer(kit.ai_label), work / "label.png")
    args: list[str] = ["-y"]
    filters: list[str] = []
    joined: list[str] = []
    inputs = 0
    for index, path in enumerate(files):
        args += ["-i", str(path)]
        video_in = inputs
        inputs += 1
        # Nothing is laid over the scene (the owner: no boxes in front of the picture); the words go in the post's caption.
        filters.append(f"[{video_in}:v]scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=increase,crop={WIDTH}:{HEIGHT},setsar=1,fps=30,format=yuv420p[v{index}]")
        if _has_audio(path):
            filters.append(f"[{video_in}:a]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo[a{index}]")
        else:
            seconds = int(scenes[index].get("seconds") or 8)
            filters.append(f"anullsrc=r=48000:cl=stereo,atrim=duration={seconds}[a{index}]")
        joined.append(f"[v{index}][a{index}]")
    args += ["-loop", "1", "-t", str(END_CARD_SECONDS), "-i", str(end_png)]
    end_in = inputs
    inputs += 1
    args += ["-i", str(label_png)]
    label_in = inputs
    filters.append(f"[{end_in}:v]scale={WIDTH}:{HEIGHT},setsar=1,fps=30,format=yuv420p[vend]")
    filters.append(f"anullsrc=r=48000:cl=stereo,atrim=duration={END_CARD_SECONDS}[aend]")
    joined.append("[vend][aend]")
    filters.append(f"{''.join(joined)}concat=n={len(files) + 1}:v=1:a=1[vall][aall]")
    filters.append(f"[vall][{label_in}:v]overlay=0:0:format=auto,format=yuv420p[vout]")
    token = secrets.token_urlsafe(24)
    out = folder("videos") / f"{token}.mp4"
    args += ["-filter_complex", ";".join(filters), "-map", "[vout]", "-map", "[aall]", "-c:v", "libx264", "-preset", "veryfast", "-crf", "21",
             "-pix_fmt", "yuv420p", "-r", "30", "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", str(out)]
    _run(args)
    cover = work / "cover.jpg"
    _run(["-y", "-ss", "1", "-i", str(out), "-frames:v", "1", "-q:v", "3", str(cover)], timeout=120)
    length = float(seconds_of(concept)) + END_CARD_SECONDS
    title = str(concept.get("title") or row.title)[:200]
    media = MarketingMedia(kind="video", source="generated", title=title, prompt=str(concept.get("idea") or "")[:4000], model=row.model, mime_type="video/mp4",
                           width=WIDTH, height=HEIGHT, size=out.stat().st_size, data=cover.read_bytes() if cover.exists() else None, file=out.name, seconds=length,
                           public_token=token, ai_generated=True, shows_people=True, alt_text=title[:500], cost_usd=row.cost_usd,
                           created_by=row.agent[:60] or "graphic_designer", created_at=datetime.now(UTC))
    session.add(media)
    session.flush()
    return media


# --- the owner's delete -------------------------------------------------------------------------------------------
def file_of(media: MarketingMedia) -> Path | None:
    if not media.file or "/" in media.file or ".." in media.file:
        return None
    path = Path(settings().marketing_dir) / "videos" / media.file
    return path if path.exists() else None


def posts_using(session: Session, media_id: int) -> list[SocialPost]:
    return [p for p in session.execute(select(SocialPost).where(SocialPost.kind == "video").order_by(SocialPost.id)).scalars() if media_id in (p.media_ids or [])]


def delete(session: Session, row: MarketingVideo) -> None:
    """The owner deletes a video's file (usually once it is posted): the posts on the networks stay; the concept stays for the record."""
    if row.status == "making":
        raise VideoRefused("This video is still being made.")
    media = session.get(MarketingMedia, row.media_id) if row.media_id else None
    if media is not None:
        waiting = [p.id for p in posts_using(session, media.id) if p.status in ("scheduled", "needs_video", "ready", "publishing")]
        if waiting:
            raise VideoRefused(f"Posts that are not out yet use this video (#{', #'.join(map(str, waiting[:5]))}); post or discard them first.")
        remove_file(media)
        session.delete(media)
    shutil.rmtree(Path(settings().marketing_dir) / "work" / str(row.id), ignore_errors=True)
    row.status, row.media_id, row.deleted_at = "deleted", None, datetime.now(UTC)


def remove_file(media: MarketingMedia) -> None:
    path = file_of(media)
    if path is not None:
        path.unlink(missing_ok=True)


def view(session: Session, row: MarketingVideo) -> dict[str, Any]:
    media = session.get(MarketingMedia, row.media_id) if row.media_id else None
    used = posts_using(session, row.media_id) if row.media_id else []
    scenes = (row.progress or {}).get("scenes") or []
    return {"id": row.id, "title": row.title, "status": row.status, "concept": row.concept, "model": row.model, "uses_character": row.uses_character,
            "scenes_done": sum(1 for s in scenes if s.get("state") == "done"), "scenes": len(scenes),
            "scene_errors": [s.get("error") for s in scenes if s.get("error")], "review": (row.progress or {}).get("review") or {}, "media_id": row.media_id, "seconds": media.seconds if media else float(seconds_of(row.concept or {})),
            "public_token": media.public_token if media else "", "cost_usd": round(row.cost_usd or 0, 2), "error": row.error, "agent": row.agent,
            "remake_from": remake_from(row.concept or {}, (row.progress or {}).get("review") or {}) if row.status == "ready" else None,
            "scene_seconds": [int(s.get("seconds") or 8) for s in scenes_of(row.concept or {})], "price_per_second": model_and_price(session)[1],
            "created_at": row.created_at, "made_at": row.made_at, "deleted_at": row.deleted_at,
            "posts": [{"id": p.id, "network": p.network, "status": p.status, "post_url": p.post_url, "posted_at": p.posted_at} for p in used]}
