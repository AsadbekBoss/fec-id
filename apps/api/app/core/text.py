"""Text normalisation for names and search (Uzbek Latin / Cyrillic / Russian)."""

from __future__ import annotations

import re
import unicodedata

# Uzbek oʻ / gʻ and the tutuq belgisi are typed with many look-alike apostrophes.
# Store and search them as one character: U+02BB (official Uzbek Latin "ʻ").
_APOSTROPHES = re.compile("['`´‘’ʻʼʹ]")
_SPACES = re.compile(r"\s+")
NAME_EXTRA_CHARS = set(" -.ʻ")


def normalize_name(value: str) -> str:
    v = unicodedata.normalize("NFC", value)
    v = _APOSTROPHES.sub("ʻ", v)
    return _SPACES.sub(" ", v).strip()


def is_valid_name(value: str) -> bool:
    """Letters (any script) plus space, hyphen, dot and ʻ; must start with a letter."""
    return (
        bool(value)
        and value[0].isalpha()
        and all(
            ch.isalpha() or unicodedata.category(ch).startswith("M") or ch in NAME_EXTRA_CHARS
            for ch in value
        )
    )


def normalize_search(value: str) -> str:
    return normalize_name(value).lower()


def escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def normalize_phone(value: str) -> str:
    """'+998 (90) 123-45-67' -> '+998901234567'. Validation happens afterwards."""
    v = value.strip()
    plus = v.startswith("+")
    digits = re.sub(r"[\s().-]", "", v.lstrip("+"))
    return ("+" if plus else "") + digits
