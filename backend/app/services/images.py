"""Uploaded pictures (company logos, profile photos): checked, then re-encoded.

An upload is never stored as sent. It must decode as a PNG, JPEG or WebP image of
a sensible size; the stored copy is re-encoded here, which drops metadata (camera,
location) and anything hidden after the image data. SVG is refused: it can carry
script.
"""

from __future__ import annotations

import io
from typing import TYPE_CHECKING

from ..core.errors import ValidationFailed

if TYPE_CHECKING:
    from PIL.Image import Image as Picture

MAX_BYTES = 1_500_000  # below the 2 MB the proxies let through
MAX_PIXELS = 25_000_000  # a larger header is refused before any pixel is decoded
MIN_SIDE = 64
FORMATS = {"PNG", "JPEG", "WEBP"}


def decode(raw: bytes, *, code: str) -> Picture:
    """The upload as an upright RGBA picture, or ValidationFailed (`<code>_...`)."""
    from PIL import Image, ImageOps, UnidentifiedImageError

    if len(raw) > MAX_BYTES:
        raise ValidationFailed("The image may be at most 1.5 MB.", code=f"{code}_TOO_LARGE")
    try:
        with Image.open(io.BytesIO(raw)) as probe:
            kind, (width, height) = probe.format, probe.size
            if kind not in FORMATS:
                raise ValidationFailed("Upload a PNG, JPEG or WebP image.", code=f"{code}_FORMAT")
            if width * height > MAX_PIXELS:
                raise ValidationFailed("The image is too large. Use at most 5000×5000 pixels.", code=f"{code}_TOO_LARGE")
            probe.verify()
        with Image.open(io.BytesIO(raw)) as image:
            image.load()
            picture = ImageOps.exif_transpose(image).convert("RGBA")
    except ValidationFailed:
        raise
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError, Image.DecompressionBombError) as exc:
        raise ValidationFailed("That file is not a readable image. Upload a PNG, JPEG or WebP.", code=f"{code}_FORMAT") from exc
    if min(picture.size) < MIN_SIDE:
        raise ValidationFailed("The image must be at least 64×64 pixels.", code=f"{code}_TOO_SMALL")
    return picture


def fit_png(picture: Picture, side: int) -> tuple[bytes, int, int]:
    """Fitted inside side×side, proportions and transparency kept (logos)."""
    from PIL import Image

    picture.thumbnail((side, side), Image.Resampling.LANCZOS)
    out = io.BytesIO()
    picture.save(out, format="PNG", optimize=True)
    return out.getvalue(), picture.width, picture.height


def square_jpeg(picture: Picture, side: int) -> bytes:
    """Cropped to the centred square and scaled to side×side, on white (photos)."""
    from PIL import Image, ImageOps

    square = ImageOps.fit(picture, (side, side), Image.Resampling.LANCZOS)
    flat = Image.new("RGB", square.size, (255, 255, 255))
    flat.paste(square, mask=square.getchannel("A"))
    out = io.BytesIO()
    flat.save(out, format="JPEG", quality=85, optimize=True)
    return out.getvalue()
