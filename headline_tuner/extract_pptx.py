"""PPTX deck extraction with python-pptx."""

from __future__ import annotations

import statistics
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE_TYPE, PP_PLACEHOLDER
from pptx.exc import PackageNotFoundError

from .errors import CorruptFileError, EmptyDeckError, TooManySlidesError
from .models import SlideContent
from .textutils import clean_block, clean_line, numeric_labels

TITLE_PLACEHOLDERS = {PP_PLACEHOLDER.TITLE, PP_PLACEHOLDER.CENTER_TITLE, PP_PLACEHOLDER.VERTICAL_TITLE}
_A_NS = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
_DIAGRAM_URI = "http://schemas.openxmlformats.org/drawingml/2006/diagram"


@dataclass
class _TextItem:
    text: str
    top: int
    left: int
    max_font_pt: float | None
    is_title_placeholder: bool = False


@dataclass
class _SlideScan:
    texts: list[_TextItem] = field(default_factory=list)
    tables: list[list[list[str]]] = field(default_factory=list)
    charts: list[str] = field(default_factory=list)
    picture_area: int = 0
    picture_count: int = 0
    unreadable_objects: int = 0


def open_pptx(path: Path):
    try:
        return Presentation(str(path))
    except (PackageNotFoundError, zipfile.BadZipFile, KeyError, ValueError) as exc:
        raise CorruptFileError("The PowerPoint file is damaged and could not be opened.") from exc
    except Exception as exc:  # lxml parse errors and similar
        raise CorruptFileError("The PowerPoint file could not be read (it may be damaged).") from exc


def _max_font_pt(text_frame) -> float | None:
    sizes: list[float] = []
    for p in text_frame.paragraphs:
        if p.font.size is not None:
            sizes.append(p.font.size.pt)
        for r in p.runs:
            if r.font.size is not None:
                sizes.append(r.font.size.pt)
    # sizes set in the text body's list style (common in templates)
    try:
        for el in text_frame._txBody.iter(f"{_A_NS}defRPr"):
            sz = el.get("sz")
            if sz:
                sizes.append(int(sz) / 100)
    except Exception:
        pass
    return max(sizes) if sizes else None


def _frame_text(text_frame) -> str:
    return clean_block("\n".join(p.text for p in text_frame.paragraphs))


def _chart_summary(shape) -> str | None:
    try:
        chart = shape.chart
    except Exception:
        return None
    parts: list[str] = []
    title = ""
    try:
        if chart.has_title and chart.chart_title.has_text_frame:
            title = clean_line(chart.chart_title.text_frame.text)
    except Exception:
        pass
    try:
        for plot in chart.plots:
            categories = [str(c) for c in plot.categories]
            for series in plot.series:
                values = list(series.values)
                pairs = []
                for i, v in enumerate(values):
                    label = categories[i] if i < len(categories) else f"point {i + 1}"
                    pairs.append(f"{label}: {'(blank)' if v is None else _fmt(v)}")
                name = clean_line(series.name or "Series")
                parts.append(f"series '{name}' — " + "; ".join(pairs))
    except Exception:
        return f"Chart{f' {title!r}' if title else ''}: data could not be read from the chart object."
    head = f"Chart {title!r}" if title else "Chart"
    return f"{head} (values from embedded chart data; units/number format not stated): " + " | ".join(parts)


def _fmt(v: float) -> str:
    return str(int(v)) if float(v).is_integer() else f"{v:g}"


def _scan_shapes(shapes, scan: _SlideScan, group_top: int | None = None, group_left: int | None = None) -> None:
    for shape in shapes:
        try:
            top = int(shape.top) if shape.top is not None else (group_top or 0)
            left = int(shape.left) if shape.left is not None else (group_left or 0)
        except Exception:
            top, left = group_top or 0, group_left or 0
        if group_top is not None:  # child coordinates are in the group's space
            top, left = group_top, group_left or 0

        st = shape.shape_type
        if st == MSO_SHAPE_TYPE.GROUP:
            _scan_shapes(shape.shapes, scan, top, left)
            continue

        is_title = False
        if shape.is_placeholder:
            try:
                is_title = shape.placeholder_format.type in TITLE_PLACEHOLDERS
            except Exception:
                is_title = False

        if getattr(shape, "has_text_frame", False) and shape.has_text_frame:
            text = _frame_text(shape.text_frame)
            if text:
                scan.texts.append(_TextItem(text, top, left, _max_font_pt(shape.text_frame), is_title))

        if getattr(shape, "has_table", False) and shape.has_table:
            rows = []
            for row in shape.table.rows:
                rows.append([clean_line(cell.text) for cell in row.cells])
            rows = [r for r in rows if any(r)]
            if rows:
                scan.tables.append(rows)
            continue

        if getattr(shape, "has_chart", False) and shape.has_chart:
            summary = _chart_summary(shape)
            if summary:
                scan.charts.append(summary)
            continue

        if st == MSO_SHAPE_TYPE.PICTURE or (shape.is_placeholder and hasattr(shape, "image")):
            scan.picture_count += 1
            try:
                scan.picture_area += int(shape.width) * int(shape.height)
            except Exception:
                pass
            continue

        if st in (MSO_SHAPE_TYPE.EMBEDDED_OLE_OBJECT, MSO_SHAPE_TYPE.LINKED_OLE_OBJECT, MSO_SHAPE_TYPE.MEDIA):
            scan.unreadable_objects += 1
            continue

        # SmartArt / diagrams are graphic frames whose text python-pptx cannot read
        try:
            xml = shape._element.xml if hasattr(shape, "_element") else ""
            if _DIAGRAM_URI in xml:
                scan.unreadable_objects += 1
        except Exception:
            pass


def _choose_title(items: list[_TextItem], slide_height: int) -> tuple[_TextItem | None, str, str]:
    for it in items:
        if it.is_title_placeholder and it.text:
            return it, "high", "title placeholder"
    if not items:
        return None, "none", ""
    top_zone = [it for it in items if it.top < slide_height * 0.4 and len(it.text.split()) <= 25]
    sized = [it for it in top_zone if it.max_font_pt]
    if sized:
        best = max(sized, key=lambda it: (it.max_font_pt, -it.top))
        others = [it.max_font_pt for it in items if it is not best and it.max_font_pt]
        if not others or best.max_font_pt >= 1.3 * statistics.median(others):
            return best, "medium", "largest text near the top (no title placeholder)"
        return best, "low", "largest text near the top (no title placeholder)"
    if top_zone:
        best = min(top_zone, key=lambda it: (it.top, it.left))
        return best, "low", "topmost text (no title placeholder or font sizes)"
    return None, "none", ""


def extract_pptx(path: Path, *, max_slides: int) -> list[SlideContent]:
    prs = open_pptx(path)
    slides_xml = list(prs.slides)
    if not slides_xml:
        raise EmptyDeckError("The presentation has no slides.")
    if len(slides_xml) > max_slides:
        raise TooManySlidesError(
            f"The deck has {len(slides_xml)} slides; the limit is {max_slides}. Remove backup "
            "slides or raise MAX_SLIDES in your configuration."
        )
    slide_w = int(prs.slide_width or 12192000)
    slide_h = int(prs.slide_height or 6858000)
    out: list[SlideContent] = []
    for number, slide in enumerate(slides_xml, start=1):
        scan = _SlideScan()
        warnings: list[str] = []
        try:
            _scan_shapes(slide.shapes, scan)
        except Exception:
            warnings.append("Some shapes on this slide could not be read; extraction may be incomplete.")
        items = sorted(scan.texts, key=lambda it: (it.top, it.left))
        title_item, confidence, source = _choose_title(items, slide_h)
        title = " ".join(title_item.text.split()) if title_item else None
        body = [it.text for it in items if it is not title_item]

        notes = ""
        try:
            if slide.has_notes_slide and slide.notes_slide.notes_text_frame is not None:
                notes = clean_block(slide.notes_slide.notes_text_frame.text)
        except Exception:
            pass

        hidden = slide._element.get("show") in {"0", "false"}
        char_count = sum(len(t) for t in body) + len(title or "")
        picture_coverage = scan.picture_area / max(slide_w * slide_h, 1)
        needs_vision = char_count < 40 and picture_coverage > 0.35
        has_visual = bool(scan.picture_count or scan.charts or scan.unreadable_objects)
        if needs_vision:
            warnings.append(
                "Slide is mostly an image (e.g., screenshot or scan) with little extractable text. "
                "The rendered slide image is used when available."
            )
        elif scan.picture_count:
            warnings.append("Contains images; text inside images was not extracted as text.")
        if scan.unreadable_objects:
            warnings.append(
                "Contains SmartArt, an embedded object, or media whose content could not be extracted."
            )
        if scan.charts:
            warnings.append("Chart values were read from the embedded chart data, not from the image.")
        if title is None:
            warnings.append("No identifiable title was found.")
        elif confidence == "low":
            warnings.append("Title detection is uncertain; please confirm the current title.")

        table_lines = [" | ".join(r) for t in scan.tables for r in t]
        out.append(
            SlideContent(
                number=number,
                title=title,
                title_confidence=confidence,  # type: ignore[arg-type]
                title_source=source,
                text=body,
                tables=scan.tables,
                chart_data=scan.charts,
                numeric_labels=numeric_labels(([title] if title else []) + body + table_lines + scan.charts),
                notes=notes,
                hidden=hidden,
                image_count=scan.picture_count,
                has_visual_content=has_visual,
                needs_vision=needs_vision,
                warnings=warnings,
            )
        )
    return out
