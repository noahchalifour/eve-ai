"""Decode, orient, strip, downscale, re-encode - once, on write.

One size serves the model and the phone (spec 2.1): 1568 px on the longest
side is the largest size vision models use without internal downscaling, and
it is plenty for a chat tile. EXIF goes because a phone photo carries GPS in
it, and neither a model nor a later download needs to know where a member's
house is.
"""

from __future__ import annotations

import io
from dataclasses import dataclass

from PIL import Image, ImageOps, UnidentifiedImageError

MAX_SIDE = 1568
MAX_BYTES = 1_000_000
_QUALITIES = (85, 75, 65, 55, 45)


@dataclass(frozen=True)
class Normalised:
    data: bytes
    content_type: str
    width: int
    height: int


def normalise(raw: bytes) -> Normalised:
    try:
        image = Image.open(io.BytesIO(raw))
        image.load()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        # DecompressionBombError is a bare Exception subclass (not OSError):
        # a tiny file can still declare an enormous pixel count, and both
        # callers (the upload route's 15MB byte cap doesn't bound pixels, and
        # from_immich) rely on normalise() never raising anything but a clean
        # ValueError.
        raise ValueError("not a decodable image") from exc

    image = ImageOps.exif_transpose(image)
    if image.mode != "RGB":
        # Flatten alpha onto white: JPEG has no alpha, and a transparent PNG
        # rendered on black is how a logo becomes a black square.
        background = Image.new("RGB", image.size, (255, 255, 255))
        rgba = image.convert("RGBA")
        background.paste(rgba, mask=rgba.split()[-1])
        image = background
    image.thumbnail((MAX_SIDE, MAX_SIDE), Image.Resampling.LANCZOS)

    # ponytail: quality steps down until the ceiling holds; a photo that
    # misses it at q45 and 1568px is noise, and shrinking once more is enough.
    for quality in _QUALITIES:
        data = _encode(image, quality)
        if len(data) <= MAX_BYTES:
            break
    else:
        image.thumbnail((MAX_SIDE // 2, MAX_SIDE // 2), Image.Resampling.LANCZOS)
        data = _encode(image, _QUALITIES[-1])
    return Normalised(data, "image/jpeg", image.width, image.height)


def _encode(image: Image.Image, quality: int) -> bytes:
    buf = io.BytesIO()
    # No `exif=` argument: Pillow writes none unless asked, which is the strip.
    image.save(buf, "JPEG", quality=quality, optimize=True)
    return buf.getvalue()
