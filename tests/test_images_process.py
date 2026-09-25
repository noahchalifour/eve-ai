"""tests/test_images_process.py - the one place bytes are reshaped. Every
guarantee the rest of the design leans on (bounded size, no EXIF location
leaking to a model or a phone, the right way up) is asserted here."""
from __future__ import annotations

import io

import pytest
from PIL import Image

from eve.images.process import MAX_BYTES, MAX_SIDE, normalise


def _jpeg(width: int, height: int, *, orientation: int | None = None) -> bytes:
    image = Image.new("RGB", (width, height), (200, 30, 30))
    exif = Image.Exif()
    if orientation is not None:
        exif[0x0112] = orientation  # Orientation
    exif[0x010F] = "Pixel 9"        # Make - must not survive
    buf = io.BytesIO()
    image.save(buf, "JPEG", exif=exif)
    return buf.getvalue()


def test_a_large_photo_is_downscaled_to_the_longest_side_bound():
    result = normalise(_jpeg(4000, 3000))
    assert max(result.width, result.height) == MAX_SIDE
    assert (result.width, result.height) == (1568, 1176)


def test_a_small_photo_is_not_upscaled():
    result = normalise(_jpeg(640, 480))
    assert (result.width, result.height) == (640, 480)


def test_exif_orientation_is_applied_then_stripped():
    # Orientation 6 = rotate 90 CW on display: a 4000x3000 sensor image is
    # really portrait. The stored bytes must be portrait with no EXIF at all.
    result = normalise(_jpeg(4000, 3000, orientation=6))
    assert (result.width, result.height) == (1176, 1568)
    stored = Image.open(io.BytesIO(result.data))
    assert len(stored.getexif()) == 0


def test_output_is_jpeg_under_the_byte_ceiling():
    noisy = Image.effect_noise((3000, 3000), 100).convert("RGB")
    buf = io.BytesIO()
    noisy.save(buf, "PNG")
    result = normalise(buf.getvalue())
    assert result.content_type == "image/jpeg"
    assert len(result.data) <= MAX_BYTES
    assert Image.open(io.BytesIO(result.data)).format == "JPEG"


def test_png_with_alpha_is_flattened_not_rejected():
    image = Image.new("RGBA", (100, 50), (0, 0, 0, 0))
    buf = io.BytesIO()
    image.save(buf, "PNG")
    assert normalise(buf.getvalue()).content_type == "image/jpeg"


def test_non_images_raise_value_error():
    with pytest.raises(ValueError):
        normalise(b"%PDF-1.7 not an image")


def test_a_declared_pixel_count_over_the_bomb_threshold_raises_value_error(monkeypatch):
    # DecompressionBombError is a bare Exception subclass, not OSError - it
    # must be caught and turned into the same ValueError as any other
    # undecodable image, or it escapes normalise()'s "never raise anything
    # but ValueError" contract. Lowering the threshold trips Pillow's own
    # safety check on an ordinary tiny image instead of allocating a real
    # ~89-megapixel one.
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 1)
    with pytest.raises(ValueError):
        normalise(_jpeg(640, 480))
