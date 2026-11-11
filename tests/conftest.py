from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any, Callable

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from headline_tuner import synthetic  # noqa: E402
from headline_tuner.config import load_settings  # noqa: E402
from headline_tuner.llm import LLMResult  # noqa: E402

ENV_VARS = [
    "ANTHROPIC_API_KEY", "ANTHROPIC_MODEL", "ANTHROPIC_EFFORT", "ANTHROPIC_THINKING", "ANTHROPIC_FALLBACKS",
    "MAX_FILE_MB", "MAX_SLIDES", "ANALYSIS_BATCH_SIZE", "VALIDATION_RETRIES", "LIBREOFFICE_PATH",
    "REVIEW_PREPARED_BY", "MAX_DECK_TEXT_CHARS", "TEN_APP_PASSWORD", "RAILWAY_ENVIRONMENT", "RAILWAY_PROJECT_ID",
    "REPORT_TEMPLATE_PATH", "RESEND_API_KEY", "NOTIFY_ENABLED", "NOTIFY_TO", "NOTIFY_FROM", "NOTIFY_REPLY_TO",
    "NOTIFY_SUBJECT_PREFIX", "APP_PUBLIC_URL", "RAILWAY_PUBLIC_DOMAIN",
]


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """Isolate tests from any real .env / environment configuration."""
    for v in ENV_VARS:
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setenv("ANTHROPIC_MODEL", "test-model")


@pytest.fixture
def settings():
    return load_settings()


@pytest.fixture(scope="session")
def deck_dir(tmp_path_factory) -> Path:
    d = tmp_path_factory.mktemp("decks")
    synthetic.build_pptx(d / "deck.pptx")
    synthetic.build_pdf(d / "deck.pdf")
    synthetic.build_long_pdf(d / "long.pdf", n=45)
    synthetic.build_encrypted_pdf(d / "encrypted.pdf")
    return d


# ---------------------------------------------------------------------------
# Fake LLM (no network). Behaviour is scripted per test.
# ---------------------------------------------------------------------------

GOOD_HEADLINE = "This slide states a clear takeaway that its own content supports for investors."


def good_review(n: int, **overrides: Any) -> dict[str, Any]:
    review = {
        "slide_number": n,
        "recommended_headline": GOOD_HEADLINE,
        "key_fact": "A fact shown on the slide",
        "significance": "Why the fact matters",
        "significance_type": "investor_significance",
        "supporting_slides": [n],
        "evidence_excerpt": "verbatim quote from the slide",
        "evidence_source": "slide_text",
        "evidence_status": "supported",
        "claim_types": ["none"],
        "evidence_gap": "",
        "conflict_note": "",
        "improvement_priority": "high",
    }
    review.update(overrides)
    return review


def default_consistency(_text: str, _call: int) -> str:
    return json.dumps({
        "company_name": "Tidewater Analytics",
        "narrative_assessment": "The titles are generic; the recommended headlines state each takeaway.",
        "headline_revisions": [],
        "narrative_gaps": ["No competition slide."],
        "appendix_summary": "Pilot detail.",
        "priority_improvements": [
            {"title": "Fix generic titles", "detail": "Replace topic labels with takeaways.", "slide_refs": [2, 3]},
        ],
    })


def requested(text: str) -> list[int]:
    m = re.search(r"REQUESTED_SLIDES: ([\d, ]+)", text)
    return [int(x) for x in m.group(1).split(",")] if m else []


class FakeLLM:
    model_name = "fake-model"

    def __init__(
        self,
        batch: Callable[[list[int], str, int], str] | None = None,
        consistency: Callable[[str, int], str] | None = None,
    ):
        self.batch = batch or (lambda nums, text, call: json.dumps({"reviews": [good_review(n) for n in nums]}))
        self.consistency = consistency or default_consistency
        self.calls: list[dict[str, Any]] = []

    def generate_json(self, *, system, content, schema, max_tokens):
        text = "\n".join(b["text"] for b in content if b.get("type") == "text")
        self.calls.append({"system": system, "content": content, "schema": schema, "text": text})
        if "headline_revisions" in schema["properties"]:
            return LLMResult(text=self.consistency(text, len(self.calls)), model=self.model_name)
        return LLMResult(text=self.batch(requested(text), text, len(self.calls)), model=self.model_name)

    @property
    def batch_calls(self):
        return [c for c in self.calls if "reviews" in c["schema"]["properties"]]


@pytest.fixture
def fake_llm_cls():
    return FakeLLM
