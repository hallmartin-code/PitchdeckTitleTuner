import pymupdf
import pytest

from headline_tuner.errors import LayoutOverflowError
from headline_tuner.pdf_report import (
    MIN_TABLE_FONT,
    ReportContent,
    auto_select,
    fits,
    page_count,
    render_pdf,
)
from headline_tuner.synthetic import long_analysis, sample_analysis


def _text(pdf: bytes) -> str:
    with pymupdf.open(stream=pdf, filetype="pdf") as doc:
        return doc[0].get_text()


def _spans(pdf: bytes):
    with pymupdf.open(stream=pdf, filetype="pdf") as doc:
        d = doc[0].get_text("dict")
    return [s for b in d["blocks"] for l in b.get("lines", []) for s in l["spans"] if s["text"].strip()]


def test_sample_renders_exactly_one_page_with_all_rows():
    content = ReportContent.from_analysis(sample_analysis())
    sel = auto_select(content)
    assert sel == [1, 2, 3, 4, 5, 6, 7, 8, 9]  # all main-deck slides fit; appendix excluded
    result = render_pdf(content, sel)
    assert page_count(result.pdf) == 1
    text = _text(result.pdf)
    assert "Tidewater Analytics" in text and "Pitch Deck Headline Review".upper() in text
    assert "tidewater_seed_deck.pptx" in text and "11 November 2026" in text
    assert "Narrative assessment" in text and "Priority improvements" in text
    assert "1 hidden, not reviewed" in text
    assert "Showing" not in text  # nothing omitted
    assert "Findings are based solely on the content of the supplied deck" in text


def test_table_text_never_below_minimum_font():
    content = ReportContent.from_analysis(sample_analysis())
    result = render_pdf(content, auto_select(content))
    assert result.table_font_size >= MIN_TABLE_FONT
    row_text = " ".join(f"{r.current_title} {r.recommended_headline}" for r in content.rows)
    table_spans = [s for s in _spans(result.pdf) if len(s["text"].strip()) > 3 and s["text"].strip() in row_text]
    assert table_spans, "expected to find table text spans"
    assert min(s["size"] for s in table_spans) >= MIN_TABLE_FONT - 0.01


def test_fonts_are_embedded_truetype():
    content = ReportContent.from_analysis(sample_analysis())
    pdf = render_pdf(content, auto_select(content)).pdf
    with pymupdf.open(stream=pdf, filetype="pdf") as doc:
        fonts = doc[0].get_fonts(full=True)
    assert fonts and all(f[1] == "ttf" for f in fonts), fonts
    assert all("DejaVu" in f[3] for f in fonts)


def test_long_deck_auto_selects_and_states_coverage():
    content = ReportContent.from_analysis(long_analysis(40))
    sel = auto_select(content)
    assert 0 < len(sel) < 40 and sel == sorted(sel)
    assert 1 in sel  # opening slide anchors the narrative
    picked = {r.slide_number: r.priority for r in content.rows if r.slide_number in sel}
    if any(p == "low" for p in picked.values()):  # low only once all high ones are in
        assert all(r.slide_number in sel for r in content.rows if r.priority == "high")
    result = render_pdf(content, sel)
    assert page_count(result.pdf) == 1
    text = " ".join(_text(result.pdf).split())
    assert f"Showing {len(sel)} of 40 main-deck slides; see the full review for all recommendations." in text


def test_overflowing_selection_is_refused_not_clipped():
    content = ReportContent.from_analysis(long_analysis(40))
    max_rows = len(auto_select(content))
    with pytest.raises(LayoutOverflowError) as exc:
        render_pdf(content, list(range(1, 41)))
    assert exc.value.requested_rows == 40 and exc.value.max_fitting_rows == max_rows
    assert "Reduce the selection" in exc.value.user_message
    # one more row than the maximum also fails, the maximum succeeds
    assert page_count(render_pdf(content, auto_select(content)).pdf) == 1


def test_font_size_below_minimum_is_not_allowed():
    content = ReportContent.from_analysis(sample_analysis())
    with pytest.raises(ValueError):
        fits(content, [1], table_font=8.5)


def test_overlong_narrative_is_reported():
    content = ReportContent.from_analysis(sample_analysis(), narrative_override="word " * 2500)
    with pytest.raises(LayoutOverflowError, match="shorten the narrative"):
        render_pdf(content, [1])


def test_unicode_and_markup_are_safe():
    a = sample_analysis()
    a.company_name = "Café Ñandú — “Zürich” € 日本"
    r = a.review(2)
    r.recommended_headline = "R&D <b>costs</b> fall ≥30% ✓ when crews use <script>alert(1)</script> sensors."
    r.current_title = "Problème & <i>Défi</i>"
    content = ReportContent.from_analysis(a)
    result = render_pdf(content, auto_select(content))
    assert page_count(result.pdf) == 1
    text = _text(result.pdf)
    for fragment in ["Café Ñandú", "€", "“Zürich”", "R&D <b>costs</b>", "≥30% ✓", "<script>", "Problème & <i>Défi</i>"]:
        assert fragment in text, fragment
    assert {"日", "本"} <= result.replaced_chars  # not in the embedded font -> substituted and reported


def test_no_company_name_uses_generic_heading():
    a = sample_analysis()
    a.company_name = None
    content = ReportContent.from_analysis(a)
    text = _text(render_pdf(content, [1, 2]).pdf)
    assert "Pitch Deck Headline Review" in text
    assert "Showing 2 of 9 main-deck slides" in " ".join(text.split())


def test_fewer_priorities_when_fewer_justified():
    a = sample_analysis()
    a.priority_improvements = a.priority_improvements[:2]
    text = _text(render_pdf(ReportContent.from_analysis(a), [1]).pdf)
    assert "1." in text and "2." in text and "Add a competition slide" not in text
