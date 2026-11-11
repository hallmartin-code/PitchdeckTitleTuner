"""Check the Anthropic API connection with your Claude Console key.

Usage:
  python scripts/check_api.py          # one tiny structured-output call (fractions of a cent)
  python scripts/check_api.py --full   # full analysis of the synthetic sample deck + one-page PDF
                                       # (about 3 model calls on a 9-slide deck; costs real tokens)

Reads ANTHROPIC_API_KEY / ANTHROPIC_MODEL and the other settings from .env or the environment,
exactly like the app. The key is never printed.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from headline_tuner.config import get_api_key, load_settings  # noqa: E402
from headline_tuner.errors import HeadlineTunerError  # noqa: E402
from headline_tuner.llm import AnthropicLLM  # noqa: E402

PING_SCHEMA = {
    "type": "object",
    "properties": {"ok": {"type": "boolean"}, "message": {"type": "string"}},
    "required": ["ok", "message"],
    "additionalProperties": False,
}


def ping(llm: AnthropicLLM) -> None:
    t = time.time()
    result = llm.generate_json(
        system="You are a connectivity check. Reply with ok=true and a five-word message.",
        content=[{"type": "text", "text": "Connectivity check."}],
        schema=PING_SCHEMA,
        max_tokens=2000,
    )
    data = json.loads(result.text)
    assert data.get("ok") is True, data
    print(f"OK  API reachable, model '{result.model}' answered in {time.time() - t:.1f}s "
          f"({result.input_tokens} in / {result.output_tokens} out tokens).")


def full(llm: AnthropicLLM, settings) -> None:
    from headline_tuner.analyzer import analyze_deck
    from headline_tuner.exports import to_html, to_json
    from headline_tuner.extraction import extract_deck
    from headline_tuner.models import AnalysisOptions
    from headline_tuner.pdf_report import ReportContent, auto_select, page_count, render_pdf
    from headline_tuner.synthetic import build_pdf

    out = ROOT / "samples" / "live"
    out.mkdir(parents=True, exist_ok=True)
    deck = build_pdf(out / "synthetic_deck.pdf")
    extraction = extract_deck(deck.read_bytes(), deck.name, settings)
    t = time.time()
    analysis = analyze_deck(extraction, AnalysisOptions(), llm, settings,
                            progress=lambda m, f: print(f"    {m}"))
    content = ReportContent.from_analysis(analysis, prepared_by=settings.prepared_by)
    result = render_pdf(content, auto_select(content))
    assert page_count(result.pdf) == 1
    (out / "live_headline_review.pdf").write_bytes(result.pdf)
    (out / "live_full_review.html").write_text(to_html(analysis), encoding="utf-8")
    (out / "live_analysis.json").write_text(to_json(analysis, extraction), encoding="utf-8")
    print(f"OK  Full analysis of {len(analysis.slide_reviews)} slides in {time.time() - t:.0f}s; "
          f"one-page PDF written to {out / 'live_headline_review.pdf'}")
    for r in analysis.main_reviews:
        print(f"    {r.slide_number:>2}. {r.recommended_headline}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--full", action="store_true", help="run a full analysis of the synthetic deck")
    args = parser.parse_args()
    try:
        settings = load_settings()
        llm = AnthropicLLM(get_api_key(), settings)
        print(f"Model: {settings.model} · effort: {settings.effort or 'default'} · thinking: {settings.thinking} · "
              f"fallbacks: {'on' if settings.fallbacks else 'off'}")
        ping(llm)
        if args.full:
            full(llm, settings)
    except HeadlineTunerError as exc:
        print(f"FAILED  {exc.user_message}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
