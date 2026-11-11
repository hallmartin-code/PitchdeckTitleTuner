"""Deterministic one-page US Letter PDF headline review (ReportLab).

The document's structure, wording, and styling come from the report template
(``templates/headline_review_template.json``, see report_template.py).

Layout is measured by laying the story out in the real frame on a scratch
canvas before rendering. Content that does not fit is never clipped or shrunk
below the minimum table font size, and no second page is ever created: the
caller is told to reduce the slide selection instead.
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from datetime import date
from xml.sax.saxutils import escape

import pymupdf
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import Frame, HRFlowable, Paragraph, Spacer, Table, TableStyle

from .errors import LayoutOverflowError
from .fonts import BODY, BODY_BOLD, HEAD_BOLD, make_drawable, register_fonts
from .models import DeckAnalysis, PriorityImprovement
from .report_template import HARD_MIN_TABLE_PT, ReportTemplate, load_template

PAGE_W, PAGE_H = letter
MIN_TABLE_FONT = HARD_MIN_TABLE_PT


@dataclass
class ReportRow:
    slide_number: int
    current_title: str
    recommended_headline: str
    flagged: bool = False
    priority: str = "medium"


@dataclass
class ReportContent:
    company_name: str | None
    source_filename: str
    total_slides: int
    appendix_count: int
    review_date: date
    narrative_assessment: str
    rows: list[ReportRow]  # every main-deck slide, in slide order
    priorities: list[PriorityImprovement]
    prepared_by: str | None = None
    template: ReportTemplate | None = None

    @property
    def main_slide_count(self) -> int:
        return len(self.rows)

    @property
    def tpl(self) -> ReportTemplate:
        if self.template is None:
            self.template = load_template()
        return self.template

    @classmethod
    def from_analysis(
        cls,
        analysis: DeckAnalysis,
        *,
        prepared_by: str | None = None,
        review_date: date | None = None,
        narrative_override: str | None = None,
        template: ReportTemplate | None = None,
    ) -> "ReportContent":
        rows = [
            ReportRow(
                slide_number=r.slide_number,
                current_title=r.current_title,
                recommended_headline=r.recommended_headline,
                flagged=r.evidence_status != "supported",
                priority=r.improvement_priority,
            )
            for r in sorted(analysis.main_reviews, key=lambda r: r.slide_number)
        ]
        return cls(
            company_name=analysis.company_name,
            source_filename=analysis.deck_filename,
            total_slides=analysis.total_slides,
            appendix_count=len(analysis.appendix_reviews),
            review_date=review_date or analysis.generated_at.date(),
            narrative_assessment=narrative_override if narrative_override is not None else analysis.narrative_assessment,
            rows=rows,
            priorities=list(analysis.priority_improvements[:5]),
            prepared_by=prepared_by,
            template=template,
        )


@dataclass
class RenderResult:
    pdf: bytes
    table_font_size: float
    shown_slides: list[int]
    replaced_chars: set[str] = field(default_factory=set)


# ---------------------------------------------------------------------------
# Geometry, colors, styles
# ---------------------------------------------------------------------------


@dataclass
class _Geometry:
    margin_x: float
    margin_top: float
    margin_bottom: float

    @property
    def content_w(self) -> float:
        return PAGE_W - 2 * self.margin_x


def _geometry(tpl: ReportTemplate) -> _Geometry:
    m = tpl.page.margins_in
    if abs(m.left - m.right) > 1e-6:
        raise ValueError("left and right margins must match")
    return _Geometry(m.left * inch, m.top * inch, m.bottom * inch)


def _colors(tpl: ReportTemplate) -> dict[str, colors.Color]:
    p = tpl.palette
    return {k: colors.HexColor(v) for k, v in p.model_dump().items()}


def _hex(c: colors.Color) -> str:
    return c.hexval().replace("0x", "#")


def _styles(tpl: ReportTemplate, table_font: float) -> dict[str, ParagraphStyle]:
    register_fonts()
    ty, c = tpl.typography, _colors(tpl)
    lead = lambda s: round(s * 1.28, 2)  # noqa: E731
    return {
        "eyebrow": ParagraphStyle("eyebrow", fontName=BODY_BOLD, fontSize=ty.eyebrow_pt, leading=lead(ty.eyebrow_pt) - 0.5, textColor=c["accent"], spaceAfter=2),
        "company": ParagraphStyle("company", fontName=HEAD_BOLD, fontSize=ty.title_pt, leading=round(ty.title_pt * 1.21, 2), textColor=c["primary"], spaceAfter=3),
        "meta": ParagraphStyle("meta", fontName=BODY, fontSize=ty.meta_pt, leading=lead(ty.meta_pt), textColor=c["muted_text"]),
        "h2": ParagraphStyle("h2", fontName=BODY_BOLD, fontSize=ty.section_heading_pt, leading=lead(ty.section_heading_pt) - 0.5, textColor=c["primary"], spaceBefore=9, spaceAfter=3),
        "body": ParagraphStyle("body", fontName=BODY, fontSize=ty.body_pt, leading=lead(ty.body_pt), textColor=colors.black, alignment=TA_LEFT),
        "note": ParagraphStyle("note", fontName=BODY, fontSize=ty.note_pt, leading=lead(ty.note_pt), textColor=c["muted_text"], spaceAfter=3),
        "legend": ParagraphStyle("legend", fontName=BODY, fontSize=ty.legend_pt, leading=lead(ty.legend_pt), textColor=c["muted_text"], spaceBefore=3),
        "th": ParagraphStyle("th", fontName=BODY_BOLD, fontSize=table_font, leading=lead(table_font), textColor=colors.white),
        "text": ParagraphStyle("text", fontName=BODY, fontSize=table_font, leading=lead(table_font), textColor=colors.black),
        "number": ParagraphStyle("number", fontName=BODY_BOLD, fontSize=table_font, leading=lead(table_font), textColor=c["primary"]),
        "headline": ParagraphStyle("headline", fontName=BODY_BOLD, fontSize=table_font, leading=lead(table_font), textColor=c["primary"]),
        "prio": ParagraphStyle(
            "prio", fontName=BODY, fontSize=ty.body_pt, leading=lead(ty.body_pt), leftIndent=15,
            bulletIndent=0, bulletFontName=BODY_BOLD, bulletFontSize=ty.body_pt, bulletColor=c["accent"], spaceAfter=2.5,
        ),
        "footer": ParagraphStyle("footer", fontName=BODY, fontSize=ty.footer_pt, leading=round(ty.footer_pt * 1.27, 2), textColor=c["muted_text"]),
    }


class _Text:
    """Escapes content for ReportLab markup and tracks replaced glyphs."""

    def __init__(self) -> None:
        self.replaced: set[str] = set()

    def __call__(self, s: str, font: str = BODY) -> str:
        drawable, replaced = make_drawable(" ".join(str(s).split()), font)
        self.replaced |= replaced
        return escape(drawable)


class _Fields(dict):
    """format_map helper: unknown {placeholders} are left visible instead of crashing."""

    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def _fmt(template: str, **values: object) -> str:
    return template.format_map(_Fields(values))


def _date(d: date) -> str:
    return d.strftime("%d %B %Y").lstrip("0")


# ---------------------------------------------------------------------------
# Story
# ---------------------------------------------------------------------------


def _footer(content: ReportContent, st: dict[str, ParagraphStyle], t: _Text) -> Paragraph:
    f = content.tpl.footer
    values = {"source_filename": content.source_filename, "review_date": _date(content.review_date),
              "prepared_by": content.prepared_by or ""}
    parts = [_fmt(f.text, **values)]
    if content.prepared_by:
        parts.append(_fmt(f.prepared_by, **values))
    parts.append(_fmt(f.generated, **values))
    return Paragraph(t(" ".join(p for p in parts if p)), st["footer"])


def _footer_height(content: ReportContent, st: dict[str, ParagraphStyle]) -> float:
    _, h = _footer(content, st, _Text()).wrap(_geometry(content.tpl).content_w, PAGE_H)
    return h + 10  # rule + gap


def _slide_count_summary(content: ReportContent) -> str:
    b = content.tpl.header.slide_count_breakdown
    total = content.total_slides
    summary = f"{total} slide{'s' if total != 1 else ''}"
    excluded = total - content.main_slide_count - content.appendix_count
    parts = []
    if content.appendix_count or excluded > 0:
        parts.append(_fmt(b.main, main_count=content.main_slide_count))
    if content.appendix_count:
        parts.append(_fmt(b.appendix, appendix_count=content.appendix_count))
    if excluded > 0:
        parts.append(_fmt(b.excluded, excluded_count=excluded))
    return summary + (f" ({', '.join(parts)})" if parts else "")


def _header(content: ReportContent, st, t: _Text) -> list:
    h = content.tpl.header
    out: list = []
    if content.company_name:
        if h.eyebrow:
            out.append(Paragraph(t(h.eyebrow, BODY_BOLD), st["eyebrow"]))
        out.append(Paragraph(t(content.company_name, HEAD_BOLD), st["company"]))
    else:
        out.append(Paragraph(t(h.title_fallback, HEAD_BOLD), st["company"]))
    values = {"source_filename": content.source_filename, "slide_count_summary": _slide_count_summary(content),
              "review_date": _date(content.review_date)}
    meta = h.meta_separator.join(_fmt(item, **values) for item in h.meta_items)
    out.append(Paragraph(t(meta), st["meta"]))
    out.append(Spacer(1, 5))
    out.append(HRFlowable(width="100%", thickness=1.2, color=_colors(content.tpl)["accent"], spaceBefore=0, spaceAfter=2))
    return out


def _narrative(content: ReportContent, section, st, t: _Text) -> list:
    text = content.narrative_assessment.strip()
    if not text and section.omit_if_empty:
        return []
    return [Paragraph(t(section.heading, BODY_BOLD), st["h2"]), Paragraph(t(text), st["body"])]


def _table(content: ReportContent, section, selected: list[int], table_font: float, st, t: _Text) -> list:
    tpl = content.tpl
    c = _colors(tpl)
    geo = _geometry(tpl)
    out: list = [Paragraph(t(section.heading, BODY_BOLD), st["h2"])]
    chosen = set(selected)
    rows = [r for r in content.rows if r.slide_number in chosen]
    notes = []
    if len(rows) < content.main_slide_count:
        notes.append(_fmt(section.coverage_note, shown=len(rows), total=content.main_slide_count))
    if content.appendix_count and section.appendix_note:
        notes.append(_fmt(section.appendix_note, appendix_count=content.appendix_count))
    if notes:
        out.append(Paragraph(t(" ".join(notes)), st["note"]))

    flag = section.flag
    data = [[Paragraph(t(col.header, BODY_BOLD), st["th"]) for col in section.columns]]
    any_flag = False
    for r in rows:
        cells = []
        for col in section.columns:
            value = t(str(getattr(r, col.field)), BODY_BOLD if col.style in {"number", "headline"} else BODY)
            if col.field == "recommended_headline" and r.flagged:
                any_flag = True
                value += f' <font color="{_hex(c["accent"])}">{t(flag.symbol)}</font>'
            cells.append(Paragraph(value, st[col.style]))
        data.append(cells)
    fixed = sum(col.width_in * inch for col in section.columns if col.width_in is not None)
    widths = [col.width_in * inch if col.width_in is not None else geo.content_w - fixed for col in section.columns]
    table = Table(data, colWidths=widths, repeatRows=0)
    table.setStyle(TableStyle([
        ("FONT", (0, 0), (-1, -1), BODY, table_font),  # replaces ReportLab's default Helvetica
        ("BACKGROUND", (0, 0), (-1, 0), c["primary"]),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, c["row_tint"]]),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3.5),
        ("LINEBELOW", (0, 1), (-1, -1), 0.4, c["rule"]),
    ]))
    out.append(table)
    if any_flag:
        out.append(Paragraph(t(_fmt(flag.legend, symbol=flag.symbol)), st["legend"]))
    return out


def _priorities(content: ReportContent, section, st, t: _Text) -> list:
    items = content.priorities[: section.max_items]
    if not items:
        return []
    grey = _hex(_colors(content.tpl)["muted_text"])
    out: list = [Paragraph(t(section.heading, BODY_BOLD), st["h2"])]
    for p in items:
        refs = ""
        if p.slide_refs:
            label = section.slide_ref_label_singular if len(p.slide_refs) == 1 else section.slide_ref_label_plural
            refs = f' <font color="{grey}">({t(label)} {", ".join(map(str, p.slide_refs))})</font>'
        sep = escape(make_drawable(section.title_detail_separator)[0])  # keep its surrounding spaces
        out.append(Paragraph(
            f"<b>{t(p.title, BODY_BOLD)}</b>{sep}{t(p.detail)}{refs}",
            st["prio"], bulletText=f"{p.rank}.",
        ))
    return out


def _story(content: ReportContent, selected: list[int], table_font: float, t: _Text) -> list:
    st = _styles(content.tpl, table_font)
    story = _header(content, st, t)
    for section in content.tpl.sections:
        if not section.enabled:
            continue
        if section.id == "narrative_assessment":
            story += _narrative(content, section, st, t)
        elif section.id == "headline_table":
            story += _table(content, section, selected, table_font, st, t)
        elif section.id == "priority_improvements":
            story += _priorities(content, section, st, t)
    return story


def _frame(content: ReportContent, footer_h: float) -> Frame:
    geo = _geometry(content.tpl)
    y = geo.margin_bottom + footer_h
    return Frame(
        geo.margin_x, y, geo.content_w, PAGE_H - geo.margin_top - y,
        leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0, showBoundary=0,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def fits(content: ReportContent, selected: list[int], table_font: float | None = None) -> bool:
    table_font = content.tpl.typography.min_table_pt if table_font is None else table_font
    if table_font < max(MIN_TABLE_FONT, content.tpl.typography.min_table_pt):
        raise ValueError("Table font may not be below the minimum size.")
    st = _styles(content.tpl, table_font)
    story = _story(content, selected, table_font, _Text())
    frame = _frame(content, _footer_height(content, st))
    scratch = Canvas(io.BytesIO(), pagesize=letter, initialFontName=BODY, initialFontSize=9)
    frame.addFromList(story, scratch)
    return not story


def auto_select(content: ReportContent) -> list[int]:
    """Highest-priority main-deck slides that fit, shown in slide order.

    The opening slide is kept when possible to anchor the narrative, then
    slides are added by improvement priority (high > medium > low) and order.
    """
    all_numbers = [r.slide_number for r in content.rows]
    if fits(content, all_numbers):
        return all_numbers
    rank = {"high": 0, "medium": 1, "low": 2}
    first = all_numbers[0] if all_numbers else None
    ordered = sorted(
        content.rows, key=lambda r: (0 if r.slide_number == first else 1, rank.get(r.priority, 1), r.slide_number)
    )
    chosen: list[int] = []
    for r in ordered:
        trial = sorted(chosen + [r.slide_number])
        if fits(content, trial):
            chosen = trial
    return chosen


def render_pdf(content: ReportContent, selected: list[int]) -> RenderResult:
    """Render exactly one page or raise LayoutOverflowError."""
    tpl = content.tpl
    valid = {r.slide_number for r in content.rows}
    selected = sorted(n for n in set(selected) if n in valid)
    chosen_font = next((f for f in tpl.typography.table_pt_steps if fits(content, selected, f)), None)
    if chosen_font is None:
        max_rows = len(auto_select(content))
        raise LayoutOverflowError(
            f"The {len(selected)} selected slides do not fit on one page at the minimum readable table size "
            f"({tpl.typography.min_table_pt:g} pt). Reduce the selection to about {max_rows} slides (or shorten "
            "the narrative assessment) and try again.",
            requested_rows=len(selected),
            max_fitting_rows=max_rows,
        )

    t = _Text()
    st = _styles(tpl, chosen_font)
    geo = _geometry(tpl)
    c_ = _colors(tpl)
    footer = _footer(content, st, t)
    footer_h = _footer_height(content, st)
    story = _story(content, selected, chosen_font, t)

    buf = io.BytesIO()
    # initialFontName avoids ReportLab's default (non-embedded) Helvetica resource.
    c = Canvas(buf, pagesize=letter, pageCompression=1, invariant=1, initialFontName=BODY, initialFontSize=9)
    base_title = tpl.header.title_fallback
    title = f"{content.company_name} — {base_title}" if content.company_name else base_title
    c.setTitle(make_drawable(title)[0])
    c.setSubject(f"Headline review of {make_drawable(content.source_filename)[0]}")
    c.setCreator("Pitch Deck Headline Tuner")
    c.setAuthor(make_drawable(content.prepared_by)[0] if content.prepared_by else "Pitch Deck Headline Tuner")

    bar = tpl.page.top_bar
    if bar.height_in > 0:
        c.setFillColor(c_["primary"])
        c.rect(0, PAGE_H - bar.height_in * inch, PAGE_W, bar.height_in * inch, stroke=0, fill=1)
        c.setFillColor(c_["accent"])
        c.rect(0, PAGE_H - bar.height_in * inch - bar.accent_height_pt, PAGE_W, bar.accent_height_pt, stroke=0, fill=1)

    frame = _frame(content, footer_h)
    frame.addFromList(story, c)
    if story:  # pragma: no cover - guarded by fits()
        raise LayoutOverflowError("Content overflowed the page.", len(selected), 0)

    c.setStrokeColor(c_["rule"])
    c.setLineWidth(0.6)
    rule_y = geo.margin_bottom + footer_h - 5
    c.line(geo.margin_x, rule_y, PAGE_W - geo.margin_x, rule_y)
    _, fh = footer.wrap(geo.content_w, PAGE_H)
    footer.drawOn(c, geo.margin_x, geo.margin_bottom + footer_h - 10 - fh)
    c.showPage()
    c.save()
    return RenderResult(pdf=buf.getvalue(), table_font_size=chosen_font, shown_slides=selected, replaced_chars=t.replaced)


def page_count(pdf: bytes) -> int:
    with pymupdf.open(stream=pdf, filetype="pdf") as doc:
        return doc.page_count


def preview_png(pdf: bytes, dpi: int = 110) -> bytes:
    with pymupdf.open(stream=pdf, filetype="pdf") as doc:
        return doc[0].get_pixmap(dpi=dpi, alpha=False).tobytes("png")


# ---------------------------------------------------------------------------
# Placeholder content: shows the template's structure with no company data
# ---------------------------------------------------------------------------


def placeholder_content(template: ReportTemplate | None = None, rows: int = 8) -> ReportContent:
    tpl = template or load_template()
    sec = tpl.section("priority_improvements")
    n_prio = sec.max_items if sec else 5
    return ReportContent(
        company_name="[Company name]",
        source_filename="[deck filename]",
        total_slides=rows + 1,
        appendix_count=1,
        review_date=date.today(),
        narrative_assessment=(
            "[Narrative assessment — at most 55 words on how well the current titles tell the story and what the "
            "recommended headlines change.]"
        ),
        rows=[
            ReportRow(
                slide_number=i,
                current_title="[Current slide title]",
                recommended_headline=(
                    "[Recommended headline — one complete sentence with an active verb, 10–18 words, "
                    "stating the slide's takeaway.]"
                ),
                flagged=(i == rows),
                priority="high",
            )
            for i in range(1, rows + 1)
        ],
        priorities=[
            PriorityImprovement(rank=i, title="[Improvement title, ≤ 8 words]",
                                detail="[What to change and why, ≤ 30 words.]", slide_refs=[i])
            for i in range(1, n_prio + 1)
        ],
        prepared_by=None,
        template=tpl,
    )
