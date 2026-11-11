"""Generate synthetic sample decks and the sample one-page PDF (no API calls).

Usage:  python scripts/make_samples.py
Outputs (in ./samples):
  tidewater_seed_deck.pptx / .pdf   synthetic decks to try in the app
  sample_headline_review.pdf        one-page review rendered from synthetic analysis
  sample_headline_review.png        preview image of that page
  sample_full_review.html / .json   supporting exports
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from headline_tuner.exports import to_html, to_json  # noqa: E402
from headline_tuner.pdf_report import (  # noqa: E402
    ReportContent,
    auto_select,
    page_count,
    placeholder_content,
    preview_png,
    render_pdf,
)
from headline_tuner.synthetic import build_pdf, build_pptx, sample_analysis  # noqa: E402


def main() -> None:
    out = ROOT / "samples"
    out.mkdir(exist_ok=True)
    build_pptx(out / "tidewater_seed_deck.pptx")
    build_pdf(out / "tidewater_seed_deck.pdf")

    analysis = sample_analysis()
    content = ReportContent.from_analysis(analysis, prepared_by=None)
    selection = auto_select(content)
    result = render_pdf(content, selection)
    assert page_count(result.pdf) == 1
    (out / "sample_headline_review.pdf").write_bytes(result.pdf)
    (out / "sample_headline_review.png").write_bytes(preview_png(result.pdf, dpi=130))
    (out / "sample_full_review.html").write_text(to_html(analysis), encoding="utf-8")
    (out / "sample_full_review.json").write_text(to_json(analysis), encoding="utf-8")
    # Structure-only preview of the report template (placeholders, no company data)
    template_dir = ROOT / "templates"
    preview = render_pdf(placeholder_content(), list(range(1, 9)))
    assert page_count(preview.pdf) == 1
    (template_dir / "headline_review_template_preview.pdf").write_bytes(preview.pdf)
    (template_dir / "headline_review_template_preview.png").write_bytes(preview_png(preview.pdf, dpi=130))
    print(f"Wrote samples to {out} (table font {result.table_font_size} pt, {len(selection)} rows)")
    print(f"Wrote template preview to {template_dir}")
    if result.replaced_chars:
        print("Replaced characters:", "".join(sorted(result.replaced_chars)))


if __name__ == "__main__":
    main()
