"""Programmatic validation of model output against the extracted deck."""

from __future__ import annotations

import json
import re
from typing import Any

from pydantic import BaseModel, ValidationError

from .errors import AnalysisError
from .models import DeckAnalysis, SlideContent, SlideReview
from .textutils import number_tokens, word_count

MIN_WORDS, MAX_WORDS = 6, 24


class RawReview(BaseModel):
    slide_number: int
    recommended_headline: str
    key_fact: str
    significance: str
    significance_type: str
    supporting_slides: list[int]
    evidence_excerpt: str
    evidence_source: str
    evidence_status: str
    claim_types: list[str]
    evidence_gap: str = ""
    conflict_note: str = ""
    improvement_priority: str = "medium"


class RawBatch(BaseModel):
    reviews: list[RawReview]


class RawRevision(BaseModel):
    slide_number: int
    revised_headline: str
    reason: str = ""


class RawPriority(BaseModel):
    title: str
    detail: str
    slide_refs: list[int] = []


class RawConsistency(BaseModel):
    company_name: str = ""
    narrative_assessment: str = ""
    headline_revisions: list[RawRevision] = []
    narrative_gaps: list[str] = []
    appendix_summary: str = ""
    priority_improvements: list[RawPriority] = []


def parse_json_model(text: str, model: type[BaseModel]) -> Any:
    """Parse model text into a Pydantic model; raise ValueError with a concise reason."""
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*|\s*```$", "", stripped)
    try:
        data = json.loads(stripped)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Response was not valid JSON ({exc.msg} at position {exc.pos}).") from exc
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()[:8]
        )
        raise ValueError(f"Response did not match the required structure: {problems}") from exc


def deck_number_tokens(slides: list[SlideContent], include_notes: bool) -> set[str]:
    tokens: set[str] = set()
    for s in slides:
        parts = [s.display_title, *s.text, s.ocr_text, *s.chart_data]
        parts += [c for t in s.tables for row in t for c in row]
        if include_notes:
            parts.append(s.notes)
        for p in parts:
            tokens |= number_tokens(p)
    return tokens


def unsupported_numbers(text: str, deck_tokens: set[str]) -> list[str]:
    """Numbers in ``text`` that do not occur anywhere in the extracted deck text."""
    return sorted(t for t in number_tokens(text) if t not in deck_tokens)


def check_review(
    raw: RawReview,
    slide: SlideContent,
    included: set[int],
    deck_tokens: set[str],
    image_slides: set[int],
) -> tuple[list[str], list[str]]:
    """Return (hard_errors, soft_warnings) for a single raw review."""
    hard: list[str] = []
    soft: list[str] = []
    n = raw.slide_number
    if not raw.recommended_headline.strip():
        hard.append(f"Slide {n}: recommended_headline is empty.")
    if not raw.evidence_excerpt.strip():
        hard.append(f"Slide {n}: evidence_excerpt is empty; quote or describe the supporting evidence.")
    bad_refs = [r for r in raw.supporting_slides if r not in included]
    if bad_refs:
        hard.append(
            f"Slide {n}: supporting_slides contains {bad_refs}, which are not included slides "
            f"(valid: {sorted(included)})."
        )
    missing = unsupported_numbers(raw.recommended_headline, deck_tokens)
    if missing and not (image_slides & set(raw.supporting_slides or [n])):
        hard.append(
            f"Slide {n}: the headline contains figure(s) {missing} that do not appear in the deck text. "
            "Use only figures from the deck, copied exactly."
        )
    elif missing:
        soft.append(
            f"Headline figure(s) {missing} were not found in extracted text (they may come from a slide "
            "image); verify against the slide."
        )
    words = word_count(raw.recommended_headline)
    if words < MIN_WORDS or words > MAX_WORDS:
        soft.append(f"Headline has {words} words (target 10–18).")
    if len(re.findall(r"[.!?](?:\s|$)", raw.recommended_headline.strip())) > 1:
        soft.append("Headline may contain more than one sentence.")
    if raw.evidence_source == "speaker_notes" and not slide.notes:
        soft.append("Evidence source is labelled as speaker notes, but this slide has no notes.")
    return hard, soft


def to_slide_review(raw: RawReview, slide: SlideContent, soft: list[str]) -> SlideReview:
    refs = sorted(set(raw.supporting_slides)) or [slide.number]
    if not raw.supporting_slides:
        soft = soft + ["No supporting slides were given; defaulted to this slide."]
    claim_types = [c for c in raw.claim_types if c in {
        "actual_result", "forecast", "target", "pilot", "signed_contract",
        "expression_of_interest", "company_claim", "none"}]
    source = raw.evidence_source
    if slide.hidden and source == "slide_text":
        source = "hidden_slide"
    return SlideReview(
        slide_number=slide.number,
        current_title=slide.display_title,  # authoritative value from extraction / user edits
        recommended_headline=" ".join(raw.recommended_headline.split()),
        key_fact=raw.key_fact,
        significance=raw.significance,
        significance_type=raw.significance_type,  # type: ignore[arg-type]
        supporting_slides=refs,
        cross_slide_support=any(r != slide.number for r in refs),
        evidence_excerpt=raw.evidence_excerpt,
        evidence_source=source,  # type: ignore[arg-type]
        evidence_status=raw.evidence_status,  # type: ignore[arg-type]
        claim_types=claim_types,  # type: ignore[arg-type]
        evidence_gap=raw.evidence_gap.strip(),
        conflict_note=raw.conflict_note.strip(),
        improvement_priority=raw.improvement_priority if raw.improvement_priority in {"high", "medium", "low"} else "medium",  # type: ignore[arg-type]
        section=slide.section,
        validation_warnings=soft,
    )


def validate_coverage(analysis: DeckAnalysis, expected: list[int]) -> None:
    """Every included slide must have exactly one review, in slide order."""
    got = [r.slide_number for r in analysis.slide_reviews]
    if sorted(got) != sorted(expected) or len(got) != len(set(got)):
        missing = sorted(set(expected) - set(got))
        extra = sorted(set(got) - set(expected))
        dupes = sorted({n for n in got if got.count(n) > 1})
        raise AnalysisError(
            "Coverage check failed: "
            + (f"missing slides {missing}; " if missing else "")
            + (f"unexpected slides {extra}; " if extra else "")
            + (f"duplicate slides {dupes}" if dupes else "")
        )
    if got != sorted(got):
        raise AnalysisError("Coverage check failed: reviews are not in slide order.")
