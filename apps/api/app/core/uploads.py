"""Validation and sanitisation of uploaded files.

* The type is decided by **content** (magic bytes plus a full decode), never by filename or the
  client's Content-Type.
* Images are fully decoded and **re-encoded**. This strips EXIF/GPS metadata and anything hidden
  after the image data, and defeats polyglot files. Decompression bombs are rejected by a pixel
  limit before decoding.
* Profile photos are normalised to an RGB JPEG of at most 512 px.
"""

from __future__ import annotations

import io
from dataclasses import dataclass

import anyio
from PIL import Image, ImageOps, UnidentifiedImageError

MAX_PIXELS = 40_000_000  # ~ 8000 x 5000
PHOTO_MAX_SIDE = 512
PHOTO_MIN_SIDE = 64
DOCUMENT_MAX_SIDE = 3000

Image.MAX_IMAGE_PIXELS = MAX_PIXELS  # Pillow raises DecompressionBombError above 2x this


class UploadRejected(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class CleanFile:
    data: bytes
    content_type: str


def _open_image(data: bytes) -> Image.Image:
    if not (data.startswith(b"\xff\xd8\xff") or data.startswith(b"\x89PNG\r\n\x1a\n")):
        raise UploadRejected("unsupported_type", "Only JPEG or PNG images are accepted.")
    try:
        img = Image.open(io.BytesIO(data))
        if img.format not in {"JPEG", "PNG"}:
            raise UploadRejected("unsupported_type", "Only JPEG or PNG images are accepted.")
        w, h = img.size
        if w * h > MAX_PIXELS:
            raise UploadRejected("too_large", "Image dimensions are too large.")
        img.load()  # full decode: truncated/corrupt files fail here
    except UploadRejected:
        raise
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError, ValueError):
        raise UploadRejected("invalid_image", "File is not a valid image.") from None
    return img


def _to_jpeg(img: Image.Image, max_side: int, quality: int) -> bytes:
    img = ImageOps.exif_transpose(img)  # apply camera rotation, then drop EXIF
    if img.mode not in ("RGB", "L"):
        background = Image.new("RGB", img.size, (255, 255, 255))
        rgba = img.convert("RGBA")
        background.paste(rgba, mask=rgba.split()[-1])
        img = background
    img.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
    out = io.BytesIO()
    img.convert("RGB").save(out, format="JPEG", quality=quality, optimize=True)  # no exif kwarg
    return out.getvalue()


def _clean_photo(data: bytes) -> CleanFile:
    img = _open_image(data)
    if min(img.size) < PHOTO_MIN_SIDE:
        raise UploadRejected("too_small", f"Image must be at least {PHOTO_MIN_SIDE}px per side.")
    return CleanFile(_to_jpeg(img, PHOTO_MAX_SIDE, 85), "image/jpeg")


def _clean_document(data: bytes) -> CleanFile:
    if data.startswith(b"%PDF-"):
        # PDFs cannot be safely re-rendered here. They are stored encrypted and served only as
        # attachment downloads with nosniff, never rendered inline by the app.
        if b"%%EOF" not in data[-2048:]:
            raise UploadRejected("invalid_document", "PDF file appears truncated.")
        return CleanFile(data, "application/pdf")
    img = _open_image(data)
    return CleanFile(_to_jpeg(img, DOCUMENT_MAX_SIDE, 90), "image/jpeg")


FACE_MAX_SIDE = 1920
FACE_MIN_SIDE = 112


def _clean_face_image(data: bytes) -> CleanFile:
    """Enrolment image: re-encoded JPEG (EXIF dropped), kept large enough for face detection.
    Used only in memory for template extraction and then discarded (never stored)."""
    img = _open_image(data)
    if min(img.size) < FACE_MIN_SIDE:
        raise UploadRejected("too_small", f"Image must be at least {FACE_MIN_SIDE}px per side.")
    return CleanFile(_to_jpeg(img, FACE_MAX_SIDE, 92), "image/jpeg")


async def clean_face_image(data: bytes, max_bytes: int) -> CleanFile:
    if len(data) > max_bytes:
        raise UploadRejected("too_large", f"File exceeds {max_bytes // (1024 * 1024)} MB.")
    if not data:
        raise UploadRejected("empty", "File is empty.")
    return await anyio.to_thread.run_sync(_clean_face_image, data)


async def clean_photo(data: bytes, max_bytes: int) -> CleanFile:
    if len(data) > max_bytes:
        raise UploadRejected("too_large", f"File exceeds {max_bytes // (1024 * 1024)} MB.")
    if not data:
        raise UploadRejected("empty", "File is empty.")
    return await anyio.to_thread.run_sync(_clean_photo, data)


async def clean_document(data: bytes, max_bytes: int) -> CleanFile:
    if len(data) > max_bytes:
        raise UploadRejected("too_large", f"File exceeds {max_bytes // (1024 * 1024)} MB.")
    if not data:
        raise UploadRejected("empty", "File is empty.")
    return await anyio.to_thread.run_sync(_clean_document, data)
