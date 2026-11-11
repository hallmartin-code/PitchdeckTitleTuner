"""Text helpers shared by extraction, validation, and rendering."""

from __future__ import annotations

import re
import unicodedata

_WS = re.compile(r"[ \t  -​]+")
_DIGIT = re.compile(r"\d")
# A numeric token: 1,200 / 3.5 / 40 (units and qualifiers are kept by
# capturing the whole surrounding line, never by rewriting the number).
NUMBER_TOKEN = re.compile(r"(?<![\w.])\d[\d,]*(?:\.\d+)?")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def clean_line(text: str) -> str:
    text = _CONTROL.sub("", text.replace("\u000b", " "))
    return _WS.sub(" ", text).strip()


def clean_block(text: str) -> str:
    lines = [clean_line(l) for l in text.replace("\r", "\n").split("\n")]
    return "\n".join(l for l in lines if l)


def numeric_labels(lines: list[str], limit: int = 60) -> list[str]:
    """Return lines that contain numbers, verbatim, so that units, currencies,
    dates, and qualifiers (e.g. "projected", "pilot", "~") stay attached."""
    seen: set[str] = set()
    out: list[str] = []
    for raw in lines:
        for line in raw.split("\n"):
            line = clean_line(line)
            if not line or not _DIGIT.search(line):
                continue
            if re.fullmatch(r"\d{1,3}", line):  # bare page numbers
                continue
            if line not in seen:
                seen.add(line)
                out.append(line)
            if len(out) >= limit:
                return out
    return out


def normalize_number(token: str) -> str:
    token = token.replace(",", "")
    if "." in token:
        token = token.rstrip("0").rstrip(".")
    return token


def number_tokens(text: str) -> set[str]:
    return {normalize_number(m.group(0)) for m in NUMBER_TOKEN.finditer(text)}


def word_count(text: str) -> int:
    return len(text.split())


def strip_accents_fallback(ch: str) -> str:
    """Best-effort ASCII-ish fallback for a single character."""
    decomposed = unicodedata.normalize("NFKD", ch)
    base = "".join(c for c in decomposed if not unicodedata.combining(c))
    return base if base and base != ch else ""


def neutralize_markup(text: str) -> str:
    """Prevent deck text from closing or opening the prompt's data delimiters."""
    return re.sub(
        r"<\s*(/?)\s*(deck|slide|instructions|system|rules)\b",
        lambda m: "‹" + m.group(1) + m.group(2),
        text,
        flags=re.IGNORECASE,
    )
