"""PDF deck extraction with PyMuPDF."""

from __future__ import annotations

import re
import statistics
from dataclasses import dataclass
from pathlib import Path

import pymupdf

from collections.abc import Callable

from .errors import CorruptFileError, EmptyDeckError, EncryptedFileError, TooManySlidesError
from .models import SlideContent
from .ocr import OCR_DPI
from .textutils import clean_block, clean_line, numeric_labels

OcrFn = Callable[[bytes], str]  # PNG bytes -> recognised text

THUMBNAIL_WIDTH_PX = 1000
VECTOR_HEAVY_THRESHOLD = 40  # drawing paths on a page that suggest a chart/diagram
LOW_TEXT_CHARS = 25


@dataclass
class _Line:
    text: str
    size: float
    y0: float
    y1: float
    x0: float
    block: int


def open_pdf(path: Path, max_slides: int) -> pymupdf.Document:
    try:
        # Open from bytes: a failed open on a path can leave the file locked on Windows.
        doc = pymupdf.open(stream=path.read_bytes(), filetype="pdf")
    except Exception as exc:  # PyMuPDF raises several types for damaged files
        raise CorruptFileError("The PDF is damaged or not a valid PDF and could not be opened.") from exc
    if doc.needs_pass or doc.is_encrypted:
        doc.close()
        raise EncryptedFileError(
            "This PDF is password-protected. Remove the password (or export an unprotected "
            "copy) and upload it again."
        )
    if doc.page_count == 0:
        doc.close()
        raise EmptyDeckError("The PDF has no pages.")
    if doc.page_count > max_slides:
        n = doc.page_count
        doc.close()
        raise TooManySlidesError(
            f"The deck has {n} pages; the limit is {max_slides}. Remove backup slides or raise "
            "MAX_SLIDES in your configuration."
        )
    return doc


def render_pdf_thumbnails(doc: pymupdf.Document, width_px: int = THUMBNAIL_WIDTH_PX) -> list[bytes | None]:
    out: list[bytes | None] = []
    for page in doc:
        try:
            zoom = width_px / max(page.rect.width, 1)
            pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
            out.append(pix.tobytes("png"))
        except Exception:
            out.append(None)
    return out


def _lines(page: pymupdf.Page) -> list[_Line]:
    data = page.get_text("dict", sort=True)
    lines: list[_Line] = []
    for b_index, block in enumerate(data.get("blocks", [])):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            spans = [s for s in line.get("spans", []) if s.get("text", "").strip()]
            if not spans:
                continue
            text = clean_line("".join(s["text"] for s in line["spans"]))
            if not text:
                continue
            size = max(float(s.get("size", 0)) for s in spans)
            x0, y0, x1, y1 = line["bbox"]
            lines.append(_Line(text, size, y0, y1, x0, b_index))
    return lines


def _choose_title(lines: list[_Line], page_height: float) -> tuple[str | None, str, str, set[int]]:
    """Return (title, confidence, source, indexes of lines used)."""
    candidates = [
        (i, l)
        for i, l in enumerate(lines)
        if any(c.isalpha() for c in l.text) and l.y0 < page_height * 0.45 and len(l.text) >= 2
    ]
    if not candidates:
        return None, "none", "", set()
    sizes = [l.size for l in lines]
    median = statistics.median(sizes)
    max_size = max(l.size for _, l in candidates)
    top_i, top = min(
        ((i, l) for i, l in candidates if l.size >= max_size - 0.5), key=lambda t: (t[1].y0, t[1].x0)
    )
    used = {top_i}
    parts = [top.text]
    last = top
    # merge wrapped title lines: same block, similar size, directly below
    for j in range(top_i + 1, len(lines)):
        nxt = lines[j]
        if nxt.block != top.block or abs(nxt.size - top.size) > 1.0:
            break
        if nxt.y0 - last.y1 > top.size * 0.9:
            break
        parts.append(nxt.text)
        used.add(j)
        last = nxt
        if len(parts) >= 3:
            break
    title = " ".join(parts)
    ratio = max_size / median if median else 1.0
    distinct_sizes = len({round(s) for s in sizes}) > 1
    if ratio >= 1.5 and top.y0 < page_height * 0.3:
        confidence = "high"
    elif ratio >= 1.15 or (len(lines) <= 3 and distinct_sizes):
        confidence = "medium"
    else:
        confidence = "low"
    if len(title.split()) > 25:
        confidence = "low"
    return title, confidence, "largest text near the top of the page", used


def extract_pdf_page(page: pymupdf.Page, number: int, *, ocr: OcrFn | None = None) -> SlideContent:
    warnings: list[str] = []
    lines = _lines(page)
    title, confidence, source, used = _choose_title(lines, page.rect.height)

    blocks: dict[int, list[str]] = {}
    for i, l in enumerate(lines):
        if i in used:
            continue
        if re.fullmatch(r"\d{1,3}", l.text) and l.y0 > page.rect.height * 0.85:
            continue  # page number in the footer
        blocks.setdefault(l.block, []).append(l.text)
    text = [clean_block("\n".join(v)) for _, v in sorted(blocks.items())]
    text = [t for t in text if t]

    tables: list[list[list[str]]] = []
    try:
        found = page.find_tables()
        for t in found.tables:
            rows = [[clean_line(c or "") for c in row] for row in t.extract()]
            rows = [r for r in rows if any(r)]
            if rows:
                tables.append(rows)
    except Exception:
        warnings.append("Table detection failed on this slide; table text is included as plain text only.")

    page_area = max(page.rect.width * page.rect.height, 1)
    image_area = 0.0
    image_count = 0
    try:
        for info in page.get_image_info():
            x0, y0, x1, y1 = info["bbox"]
            image_area += max(0.0, (x1 - x0)) * max(0.0, (y1 - y0))
            image_count += 1
    except Exception:
        pass
    image_coverage = min(image_area / page_area, 1.0)
    try:
        drawings = len(page.get_drawings())
    except Exception:
        drawings = 0

    char_count = sum(len(l.text) for l in lines)
    has_visual = image_count > 0 or drawings >= VECTOR_HEAVY_THRESHOLD
    needs_vision = False
    if char_count < LOW_TEXT_CHARS and (image_coverage > 0.3 or drawings >= 10):
        needs_vision = True
        warnings.append(
            "Little or no extractable text: the slide appears to be image-based (scan, screenshot, "
            "or diagram). Its rendered image is used instead; text inside images may be incomplete."
        )
    elif char_count == 0:
        warnings.append("No text or images were found; the slide appears blank.")
    if drawings >= VECTOR_HEAVY_THRESHOLD:
        warnings.append(
            "Contains a chart or diagram drawn as vector graphics. Only printed labels were extracted; "
            "values that are not printed as text were not reviewed."
        )
    elif image_count and not needs_vision and image_coverage > 0.15:
        warnings.append("Contains images; any text inside images was not extracted as text.")

    ocr_text = ""
    if ocr is not None and needs_vision:
        try:
            ocr_text = ocr(page.get_pixmap(dpi=OCR_DPI, alpha=False).tobytes("png"))
            if ocr_text:
                warnings.append("Text was recovered with OCR; OCR text may contain recognition errors.")
        except Exception:
            warnings.append("OCR failed on this slide.")

    if title is None:
        warnings.append("No identifiable title was found.")
    elif confidence == "low":
        warnings.append("Title detection is uncertain; please confirm the current title.")

    return SlideContent(
        number=number,
        title=title,
        title_confidence=confidence,  # type: ignore[arg-type]
        title_source=source,
        text=text,
        tables=tables,
        numeric_labels=numeric_labels(([title] if title else []) + text + [" | ".join(r) for t in tables for r in t] + ([ocr_text] if ocr_text else [])),
        ocr_text=ocr_text,
        image_count=image_count,
        has_visual_content=has_visual,
        needs_vision=needs_vision,
        warnings=warnings,
    )


def extract_pdf(
    path: Path, *, max_slides: int, render: bool = True, ocr: OcrFn | None = None
) -> tuple[list[SlideContent], bool]:
    """Extract all pages of a PDF. Returns (slides, rendering_available)."""
    doc = open_pdf(path, max_slides)
    try:
        slides = [extract_pdf_page(page, i + 1, ocr=ocr) for i, page in enumerate(doc)]
        rendered = False
        if render:
            thumbs = render_pdf_thumbnails(doc)
            for slide, png in zip(slides, thumbs):
                slide.thumbnail_png = png
            rendered = any(t is not None for t in thumbs)
        return slides, rendered
    finally:
        doc.close()
