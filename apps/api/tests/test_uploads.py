"""Upload sanitisation (no database)."""

import io

import pytest
from PIL import Image

from app.core import uploads


def _img(
    fmt: str = "JPEG", size: tuple[int, int] = (800, 600), mode: str = "RGB", exif: bool = False
) -> bytes:
    im = Image.new(mode, size, (200, 100, 50) if mode == "RGB" else (200, 100, 50, 128))
    out = io.BytesIO()
    kw = {}
    if exif:
        ex = Image.Exif()
        ex[0x010F] = "SecretCameraMaker"  # Make
        ex[0x8825] = {2: (41.0, 18.0, 0.0)}  # GPS IFD
        kw["exif"] = ex.tobytes()
    im.save(out, format=fmt, **kw)
    return out.getvalue()


async def test_photo_reencoded_resized_and_exif_stripped() -> None:
    raw = _img(exif=True, size=(2000, 1500))
    assert b"SecretCameraMaker" in raw
    clean = await uploads.clean_photo(raw, 5_000_000)
    assert clean.content_type == "image/jpeg"
    assert b"SecretCameraMaker" not in clean.data
    out = Image.open(io.BytesIO(clean.data))
    assert out.format == "JPEG" and max(out.size) == 512
    assert not out.getexif()


async def test_png_with_alpha_becomes_jpeg() -> None:
    clean = await uploads.clean_photo(_img("PNG", mode="RGBA"), 5_000_000)
    assert Image.open(io.BytesIO(clean.data)).format == "JPEG"


async def test_polyglot_trailing_payload_removed() -> None:
    raw = _img() + b"PK\x03\x04<script>alert(1)</script>"
    clean = await uploads.clean_photo(raw, 5_000_000)
    assert b"<script>" not in clean.data


@pytest.mark.parametrize(
    ("data", "code"),
    [
        (b"", "empty"),
        (b"hello world, not an image", "unsupported_type"),
        (b"GIF89a" + b"\x00" * 100, "unsupported_type"),
        (b"\xff\xd8\xff\xe0" + b"\x00" * 50, "invalid_image"),  # truncated JPEG
    ],
)
async def test_photo_rejections(data: bytes, code: str) -> None:
    with pytest.raises(uploads.UploadRejected) as e:
        await uploads.clean_photo(data, 5_000_000)
    assert e.value.code == code


async def test_photo_too_small_and_too_big() -> None:
    with pytest.raises(uploads.UploadRejected) as e:
        await uploads.clean_photo(_img(size=(20, 20)), 5_000_000)
    assert e.value.code == "too_small"
    with pytest.raises(uploads.UploadRejected) as e:
        await uploads.clean_photo(_img(), 100)
    assert e.value.code == "too_large"


async def test_decompression_bomb_rejected() -> None:
    # A tiny PNG that declares a huge canvas.
    bomb = _img("PNG", size=(10000, 10000), mode="RGB")
    with pytest.raises(uploads.UploadRejected):
        await uploads.clean_photo(bomb, 50_000_000)


async def test_documents() -> None:
    pdf = b"%PDF-1.7\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"
    clean = await uploads.clean_document(pdf, 10_000_000)
    assert clean.content_type == "application/pdf" and clean.data == pdf
    with pytest.raises(uploads.UploadRejected):
        await uploads.clean_document(b"%PDF-1.7 truncated", 10_000_000)
    img = await uploads.clean_document(_img(exif=True), 10_000_000)
    assert img.content_type == "image/jpeg" and b"SecretCameraMaker" not in img.data
