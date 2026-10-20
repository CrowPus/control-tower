"""The picture studio: professional pictures for posts and ads, made with
Gemini's image model in the brand's style, finished here with the logo, an optional headline band in the
brand's colours, and the AI label on every made picture (the owner's decision). Each picture's cost
counts against the owner's monthly picture budget; without a budget none is made.

A picture may show the company's character (character.py): its pictures go to the model as references, so the
person stays the same while clothes and place change. Videos are made by the video studio (video_studio.py).
"""

from __future__ import annotations

import io
import secrets
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont, ImageOps
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ...ai.providers import gemini_key
from ...config import settings
from ...models import AIUsage, MarketingMedia
from . import brand

ASPECTS = ("1:1", "4:5", "9:16", "16:9", "3:4", "4:3", "2:3", "3:2")
OPERATION = "marketing.image"
# Safety rails, not business values: the largest picture kept and the size an upload may have.
MAX_SIDE = 2048
MAX_UPLOAD = 15 * 1024 * 1024

# What every prompt carries, whatever the agent asks for: professional, honest, nothing that is not ours.
RULES = ("Natural, credible, diverse people where people appear, adults only. No text or letters in the image unless explicitly asked for. "
         "No brand names, logos or trademarks of other companies anywhere (vehicles, clothing, buildings, screens). No real, famous or "
         "identifiable persons. No flags, symbols or uniforms of authorities.")


def house_rules(kit: brand.BrandKit) -> str:
    what = f" ({kit.tagline})" if kit.tagline else ""
    return f"Professional photograph or clean graphic for the social media of {kit.name}{what}. {RULES}"


class StudioRefused(ValueError):
    """The picture cannot be made; the reason is shown (to the agent or the owner)."""


@dataclass
class Made:
    data: bytes
    mime_type: str
    model: str


def _generate(prompt: str, aspect: str, model: str, references: Sequence[tuple[bytes, str]] = ()) -> Made:
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=gemini_key())
    contents: Any = [*(types.Part.from_bytes(data=data, mime_type=mime) for data, mime in references), prompt] if references else prompt
    response = client.models.generate_content(model=model, contents=contents,
                                              config=types.GenerateContentConfig(response_modalities=["IMAGE"], image_config=types.ImageConfig(aspect_ratio=aspect)))
    for candidate in response.candidates or []:
        for part in (candidate.content.parts if candidate.content else []) or []:
            if part.inline_data and part.inline_data.data:
                return Made(data=part.inline_data.data, mime_type=part.inline_data.mime_type or "image/png", model=model)
    raise StudioRefused("The image model returned no picture (it may have declined the request). Describe the picture differently.")


# Tests replace this: (prompt, aspect, model[, references]) -> Made.
generator: Callable[..., Made] = _generate


def spent_this_month(session: Session) -> float:
    start = datetime.now(UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return float(session.execute(select(func.coalesce(func.sum(AIUsage.estimated_cost_usd), 0.0))
                                 .where(AIUsage.operation == OPERATION, AIUsage.created_at >= start)).scalar() or 0.0)


def budget(session: Session) -> dict[str, Any]:
    cap = brand.settings_of(session).monthly_picture_budget_usd
    spent = spent_this_month(session)
    return {"monthly_budget_usd": cap, "spent_this_month_usd": round(spent, 2), "left_usd": None if cap is None else round(max(cap - spent, 0), 2)}


def _hex(color: str) -> tuple[int, int, int]:
    return int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16)


FONTS = Path(__file__).resolve().parent / "fonts"  # Noto Sans (SIL Open Font License, fonts/OFL.txt): German, Turkish, Polish, Ukrainian …


def _font(size: int, *, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    try:
        return ImageFont.truetype(str(FONTS / ("NotoSans-Bold.ttf" if bold else "NotoSans-Regular.ttf")), size)
    except OSError:
        return ImageFont.load_default(size=size)


def _wrap(draw: ImageDraw.ImageDraw, text: str, font: Any, width: int) -> list[str]:
    lines: list[str] = []
    for paragraph in text.splitlines() or [""]:
        line = ""
        for word in paragraph.split():
            trial = f"{line} {word}".strip()
            if draw.textlength(trial, font=font) <= width or not line:
                line = trial
            else:
                lines.append(line)
                line = word
        lines.append(line)
    return [x for x in lines if x][:4]


def finish(data: bytes, kit: brand.BrandKit, *, logo: bytes | None, headline: str = "", subline: str = "", label: bool = True) -> tuple[bytes, int, int]:
    """The made picture as a JPEG with the brand's touches: a headline band, the logo, the AI label."""
    image = ImageOps.exif_transpose(Image.open(io.BytesIO(data))).convert("RGB")
    image.thumbnail((MAX_SIDE, MAX_SIDE))
    width, height = image.size
    draw = ImageDraw.Draw(image, "RGBA")
    unit = max(12, width // 40)
    if headline:
        big, small = _font(int(unit * 2.0), bold=True), _font(int(unit * 1.2))
        inner = width - 4 * unit
        head_lines = _wrap(draw, headline, big, inner)
        sub_lines = _wrap(draw, subline, small, inner) if subline else []
        line_h, sub_h = int(unit * 2.6), int(unit * 1.6)
        band = 2 * unit + len(head_lines) * line_h + (len(sub_lines) * sub_h + unit // 2 if sub_lines else 0) + unit
        top = height - band
        draw.rectangle([0, top, width, height], fill=(*_hex(kit.primary_color), 235))
        draw.rectangle([2 * unit, top + unit, 2 * unit + 3 * unit, top + unit + max(3, unit // 4)], fill=_hex(kit.accent_color))
        y = top + int(unit * 1.6)
        for line in head_lines:
            draw.text((2 * unit, y), line, font=big, fill=_hex(kit.text_color))
            y += line_h
        y += unit // 2
        for line in sub_lines:
            draw.text((2 * unit, y), line, font=small, fill=_hex(kit.text_color))
            y += sub_h
    if logo and kit.logo_position != "none":
        mark = ImageOps.exif_transpose(Image.open(io.BytesIO(logo))).convert("RGBA")
        target = max(32, width // 13)
        mark.thumbnail((target * 3, target))
        pad = unit
        x = pad if kit.logo_position.endswith("left") else width - mark.width - pad
        bottom_reserved = (height - (top if headline else height)) if headline else 0
        y0 = pad if kit.logo_position.startswith("top") else height - bottom_reserved - mark.height - pad
        plate = Image.new("RGBA", (mark.width + unit, mark.height + unit), (255, 255, 255, 215))
        image.paste(plate, (x - unit // 2, y0 - unit // 2), plate)
        image.paste(mark, (x, y0), mark)
    if label and kit.ai_label:
        font = _font(max(11, unit // 2 + 3))
        text_w = int(draw.textlength(kit.ai_label, font=font))
        box_h = int(unit * 1.1)
        bx, by = width - text_w - unit, (top if headline else height) - box_h - unit // 3
        draw.rectangle([bx - unit // 3, by, bx + text_w + unit // 3, by + box_h], fill=(0, 0, 0, 140))
        draw.text((bx, by + box_h // 6), kit.ai_label, font=font, fill=(255, 255, 255, 235))
    out = io.BytesIO()
    image.save(out, format="JPEG", quality=90, optimize=True, progressive=True)
    return out.getvalue(), width, height


def make(session: Session, *, prompt: str, aspect: str = "4:5", title: str = "", headline: str = "", subline: str = "", shows_people: bool = False,
         alt_text: str = "", created_by: str = "", with_character: bool = False) -> MarketingMedia:
    """Make one picture in the brand's style (with the company's character when asked); StudioRefused with the reason when it cannot be made."""
    prompt = prompt.strip()
    if len(prompt) < 15:
        raise StudioRefused("Describe the picture in a sentence or two (who or what, where, the mood).")
    if aspect not in ASPECTS:
        raise StudioRefused(f"The format is one of {', '.join(ASPECTS)} (4:5 for Instagram and Facebook feeds, 1:1, 9:16 for stories, 16:9 for LinkedIn and YouTube).")
    if len(headline) > 90 or len(subline) > 140:
        raise StudioRefused("A headline has at most 90 characters, the line under it 140.")
    wanted = brand.settings_of(session)
    cap = wanted.monthly_picture_budget_usd
    best = wanted.picture_quality == "best"
    cost = settings().ai_image_price_best_usd if best else settings().ai_image_price_usd
    if cap is None:
        raise StudioRefused("The owner has not set a monthly picture budget yet (Admin → Marketing → Brand), so no picture is made.")
    if spent_this_month(session) + cost > cap:
        raise StudioRefused(f"This month's picture budget of ${cap:.2f} is used up; the owner can raise it in Admin → Marketing → Brand.")
    kit = brand.kit(session)
    model = settings().gemini_image_model_best if best else settings().gemini_image_model
    full = f"{house_rules(kit)}\nPicture style: {kit.image_style}\n\nThe picture: {prompt}" if kit.image_style else f"{house_rules(kit)}\n\nThe picture: {prompt}"
    references: list[tuple[bytes, str]] = []
    if with_character:
        from . import character

        figure = character.get(session)
        references = [(m.data or b"", m.mime_type) for m in character.pictures(session, figure)]
        if not figure.ready or not references:
            raise StudioRefused("The company's character is not set up yet (Admin → Marketing → Character: its name and pictures).")
        full = (f"{full}\n\n{character.describe(figure)}\nThe reference pictures show {figure.name}; show exactly this person (same face, hair, skin, "
                "build, age) in the scene described — the clothes may differ.")
        shows_people = True
    started = time.monotonic()
    try:
        made = generator(full, aspect, model, references) if references else generator(full, aspect, model)
    except StudioRefused:
        _usage(session, model, 0.0, started, success=False, error="declined")
        raise
    except Exception as exc:  # noqa: BLE001 - the model's failures are shown as a reason, never raised further
        _usage(session, model, 0.0, started, success=False, error=exc.__class__.__name__)
        raise StudioRefused(f"The image model failed ({exc.__class__.__name__}); try again in a moment.") from exc
    logo = None
    if kit.logo_media_id:
        logo_row = session.get(MarketingMedia, kit.logo_media_id)
        logo = logo_row.data if logo_row and logo_row.kind == "image" else None
    data, width, height = finish(made.data, kit, logo=logo, headline=headline.strip(), subline=subline.strip(), label=True)
    _usage(session, model, cost, started, success=True)
    row = MarketingMedia(kind="image", source="generated", title=(title or headline or prompt)[:200], prompt=prompt[:4000], model=model, mime_type="image/jpeg",
                         width=width, height=height, size=len(data), data=data, public_token=secrets.token_urlsafe(24), ai_generated=True,
                         shows_people=shows_people, alt_text=(alt_text or title or prompt)[:500], cost_usd=cost, created_by=created_by[:60], created_at=datetime.now(UTC))
    session.add(row)
    session.flush()
    return row


def frame(session: Session, *, moment: str, outfit: str, setting: str, continue_from: int | None = None, created_by: str = "") -> MarketingMedia:
    """A storyboard frame: the first moment of one video scene with the company's character, in the
    video's outfit and place, vertical 9:16 and without logo, label or words — it becomes the scene's first frame, so the
    person, clothes and place stay the same from scene to scene. The frame of the scene before can be given, so the next one
    matches it. From the picture budget; never posted on its own."""
    from . import character

    moment, outfit, setting = moment.strip(), outfit.strip(), setting.strip()
    if len(moment) < 15 or len(outfit) < 5 or len(setting) < 5:
        raise StudioRefused("Describe the moment (who does what, the framing), the outfit and the place.")
    figure = character.get(session)
    references = [(m.data or b"", m.mime_type) for m in character.pictures(session, figure)]
    if not figure.ready or not references:
        raise StudioRefused("The company's character is not set up yet (Admin → Marketing → Character: its name and pictures).")
    before = session.get(MarketingMedia, continue_from) if continue_from else None
    if continue_from and (before is None or before.source != "storyboard" or not before.data):
        raise StudioRefused("continue_from is the media_id of an earlier storyboard frame.")
    wanted = brand.settings_of(session)
    cap = wanted.monthly_picture_budget_usd
    best = wanted.picture_quality == "best"
    cost = settings().ai_image_price_best_usd if best else settings().ai_image_price_usd
    if cap is None:
        raise StudioRefused("The owner has not set a monthly picture budget yet (Admin → Marketing → Brand), so no frame is made.")
    if spent_this_month(session) + cost > cap:
        raise StudioRefused(f"This month's picture budget of ${cap:.2f} is used up; the owner can raise it in Admin → Marketing → Brand.")
    model = settings().gemini_image_model_best if best else settings().gemini_image_model
    kit = brand.kit(session)
    prompt = (f"{house_rules(kit)}\nA photorealistic still from a vertical 9:16 short video (the first frame of a scene)."
              f"{' Visual style: ' + kit.image_style if kit.image_style else ''}\n\n{character.describe(figure)}\n"
              f"The reference pictures show {figure.name}: exactly this person (same face, hair, skin, build, age).\n"
              f"{figure.name} wears: {outfit}.\nPlace: {setting}.\nThe moment: {moment}\n"
              "Natural hands, simple pose, no text, letters, logos or readable screens anywhere.")
    if before is not None:
        references = [*references, (before.data or b"", before.mime_type)]
        prompt += (f"\nThe last reference picture is the frame of the scene before: keep {figure.name}'s clothes, the place and the light "
                   "exactly as there.")
    started = time.monotonic()
    try:
        made = generator(prompt, "9:16", model, references)
    except StudioRefused:
        _usage(session, model, 0.0, started, success=False, error="declined")
        raise
    except Exception as exc:  # noqa: BLE001 - the model's failures are shown as a reason, never raised further
        _usage(session, model, 0.0, started, success=False, error=exc.__class__.__name__)
        raise StudioRefused(f"The image model failed ({exc.__class__.__name__}); try again in a moment.") from exc
    image = ImageOps.exif_transpose(Image.open(io.BytesIO(made.data))).convert("RGB")
    image.thumbnail((MAX_SIDE, MAX_SIDE))
    out = io.BytesIO()
    image.save(out, format="JPEG", quality=92)
    data = out.getvalue()
    _usage(session, model, cost, started, success=True)
    row = MarketingMedia(kind="image", source="storyboard", title=f"Storyboard: {moment}"[:200], prompt=prompt[:4000], model=model, mime_type="image/jpeg",
                         width=image.width, height=image.height, size=len(data), data=data, public_token=secrets.token_urlsafe(24), ai_generated=True,
                         shows_people=True, alt_text=moment[:500], cost_usd=cost, created_by=created_by[:60], created_at=datetime.now(UTC))
    session.add(row)
    session.flush()
    return row


def _usage(session: Session, model: str, cost: float, started: float, *, success: bool, error: str = "") -> None:
    session.add(AIUsage(agent_type="team", model=model, operation=OPERATION, input_tokens=0, output_tokens=0, estimated_cost_usd=cost,
                        latency_ms=int((time.monotonic() - started) * 1000), success=success, error_class=error[:80], created_at=datetime.now(UTC)))


def upload(session: Session, *, data: bytes, filename: str, title: str = "", alt_text: str = "", created_by: str = "") -> MarketingMedia:
    """A picture the owner uploads (a real photo, the logo): checked, kept as JPEG or PNG (PNG keeps a logo's transparency)."""
    if not data or len(data) > MAX_UPLOAD:
        raise StudioRefused(f"A picture may be at most {MAX_UPLOAD // (1024 * 1024)} MB.")
    try:
        image = ImageOps.exif_transpose(Image.open(io.BytesIO(data)))
        image.load()
    except Exception as exc:  # noqa: BLE001 - anything Pillow cannot read is not a picture
        raise StudioRefused("This is not a picture the studio can use (JPEG, PNG or WebP).") from exc
    if image.format not in ("JPEG", "PNG", "WEBP", "MPO") and image.format is not None:
        raise StudioRefused("Use a JPEG, PNG or WebP picture.")
    image.thumbnail((MAX_SIDE, MAX_SIDE))
    out = io.BytesIO()
    transparent = image.mode in ("RGBA", "LA", "P")
    if transparent:
        image.convert("RGBA").save(out, format="PNG", optimize=True)
        mime = "image/png"
    else:
        image.convert("RGB").save(out, format="JPEG", quality=92, optimize=True)
        mime = "image/jpeg"
    content = out.getvalue()
    row = MarketingMedia(kind="image", source="upload", title=(title or filename)[:200], mime_type=mime, width=image.width, height=image.height, size=len(content),
                         data=content, public_token=secrets.token_urlsafe(24), ai_generated=False, alt_text=(alt_text or title or filename)[:500],
                         created_by=created_by[:60], created_at=datetime.now(UTC))
    session.add(row)
    session.flush()
    return row


def link_video(session: Session, *, link: str, title: str = "", created_by: str = "") -> MarketingMedia:
    """A video the owner made, by its link (Google Drive, Dropbox or a direct file address); fetched when posted."""
    from ..social import video

    direct = video.direct_link(link)
    if not direct:
        raise StudioRefused("Give a link that starts with https:// and leads to the video file (a shared Google Drive or Dropbox link works).")
    row = MarketingMedia(kind="video", source="link", title=(title or "Video")[:200], mime_type="video/mp4", link=direct[:1000], public_token=secrets.token_urlsafe(24),
                         created_by=created_by[:60], created_at=datetime.now(UTC))
    session.add(row)
    session.flush()
    return row


def public_url(row: MarketingMedia) -> str:
    """The address the networks fetch a picture from (Instagram and Facebook need one)."""
    base = (settings().public_base_url or "").rstrip("/")
    extension = "png" if row.mime_type == "image/png" else "jpg" if row.kind == "image" else "mp4"
    return f"{base}/api/v1/media/{row.public_token}.{extension}" if base else ""
