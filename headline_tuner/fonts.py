"""Embedded TrueType font registration and glyph-coverage helpers for ReportLab.

DejaVu Sans (bundled in assets/fonts, Bitstream Vera / public-domain style
license) covers Latin, Greek, Cyrillic, and common symbols. Characters the font
cannot draw are replaced with a close equivalent or "?" and reported, so the
PDF never shows empty boxes silently.
"""

from __future__ import annotations

import os
import unicodedata
from functools import lru_cache
from pathlib import Path

from reportlab.lib.fonts import addMapping
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

from .config import PROJECT_ROOT
from .textutils import strip_accents_fallback

FONT_DIR = PROJECT_ROOT / "assets" / "fonts"

BODY = "HTBody"
BODY_BOLD = "HTBody-Bold"
HEAD_BOLD = "HTHead-Bold"

_FILES = {
    BODY: "DejaVuSansCondensed.ttf",
    BODY_BOLD: "DejaVuSansCondensed-Bold.ttf",
    HEAD_BOLD: "DejaVuSans-Bold.ttf",
}

# Close equivalents for characters that are often missing from fonts.
_REPLACEMENTS = {
    "‐": "-", "‑": "-", "‒": "-", "−": "-",
    "‘": "'", "’": "'", "“": '"', "”": '"',
    "…": "...", " ": " ", " ": " ", " ": " ",
}


def _font_path(name: str) -> Path:
    override = os.environ.get("PDF_FONT_DIR")
    base = Path(override) if override else FONT_DIR
    path = base / _FILES[name]
    if not path.is_file() and override:
        path = FONT_DIR / _FILES[name]
    return path


@lru_cache(maxsize=1)
def register_fonts() -> None:
    for name in _FILES:
        path = _font_path(name)
        if not path.is_file():
            raise FileNotFoundError(
                f"Font file missing: {path}. Reinstall the project files (assets/fonts)."
            )
        pdfmetrics.registerFont(TTFont(name, str(path)))
    addMapping(BODY, 0, 0, BODY)
    addMapping(BODY, 1, 0, BODY_BOLD)
    addMapping(BODY, 0, 1, BODY)
    addMapping(BODY, 1, 1, BODY_BOLD)


@lru_cache(maxsize=8)
def _coverage(font_name: str) -> frozenset[int]:
    register_fonts()
    font = pdfmetrics.getFont(font_name)
    return frozenset(font.face.charToGlyph.keys())


def make_drawable(text: str, font_name: str = BODY) -> tuple[str, set[str]]:
    """Return text the font can draw, plus the set of characters that were replaced."""
    covered = _coverage(font_name)
    out: list[str] = []
    replaced: set[str] = set()
    for ch in text:
        if ch in "\n\t" or ord(ch) in covered:
            out.append(ch)
            continue
        if unicodedata.category(ch) in {"Cc", "Cf"}:
            continue  # invisible control/format characters
        sub = _REPLACEMENTS.get(ch)
        if sub is None:
            nfkc = unicodedata.normalize("NFKC", ch)
            if nfkc != ch and all(ord(c) in covered for c in nfkc):
                sub = nfkc
            else:
                base = strip_accents_fallback(ch)
                sub = base if base and all(ord(c) in covered for c in base) else "?"
        replaced.add(ch)
        out.append(sub)
    return "".join(out), replaced
