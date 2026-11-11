"""Deck analysis pipeline.

Stages:
1. Per-slide review in batches (every batch sees the whole deck text so
   cross-slide evidence works; images are attached for the batch's slides).
2. Programmatic validation with bounded retries for malformed or incomplete
   output, re-requesting only the slides that failed.
3. A deck-wide narrative consistency pass that may revise headlines without
   adding claims, and produces the narrative assessment, gaps, and priorities.
4. A final coverage check: one review for every included slide, in order.
"""

from __future__ import annotations

import base64
import json
import logging
from collections.abc import Callable
from typing import Any

from .config import Settings
from .errors import AnalysisError
from .llm import LLMClient, MalformedResponseError
from .models import (
    AnalysisOptions,
    DeckAnalysis,
    DeckExtraction,
    PriorityImprovement,
    SlideContent,
    SlideReview,
)
from .prompts import (
    BATCH_SCHEMA,
    CONSISTENCY_SCHEMA,
    SYSTEM_PROMPT,
    batch_task,
    consistency_task,
    deck_text,
    outline_text,
)
from .validation import (
    RawBatch,
    RawConsistency,
    check_review,
    deck_number_tokens,
    parse_json_model,
    to_slide_review,
    unsupported_numbers,
    validate_coverage,
)

log = logging.getLogger(__name__)

Progress = Callable[[str, float], None]
BATCH_MAX_TOKENS = 32000
CONSISTENCY_MAX_TOKENS = 24000


def _noop(_: str, __: float) -> None:
    pass


def _chunks(items: list[int], size: int) -> list[list[int]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


def _image_block(png: bytes) -> dict[str, Any]:
    return {
        "type": "image",
        "source": {"type": "base64", "media_type": "image/png", "data": base64.standard_b64encode(png).decode("ascii")},
    }


def collect_limitations(
    extraction: DeckExtraction,
    included: list[SlideContent],
    options: AnalysisOptions,
    image_slides: set[int],
    full_text_used: bool,
) -> list[str]:
    out = list(extraction.warnings)
    hidden = [s.number for s in extraction.slides if s.hidden]
    if hidden and not options.include_hidden:
        out.append(f"Hidden slides were excluded by default: {', '.join(map(str, hidden))}.")
    if any(s.notes for s in extraction.slides) and not options.include_notes:
        out.append("Speaker notes were excluded by default.")
    for s in included:
        relevant = [w for w in s.warnings if "uncertain" not in w.lower()]
        if s.needs_vision and s.number not in image_slides:
            relevant.append("Image-based slide was not visible to the model; review is limited to extracted text.")
        for w in relevant:
            out.append(f"Slide {s.number}: {w}")
    if not full_text_used:
        out.append(
            "The deck text exceeded the configured size limit, so each batch saw full text only for its own "
            "slides plus an outline of the rest; cross-slide evidence may be incomplete."
        )
    # de-duplicate, keep order
    seen: set[str] = set()
    return [x for x in out if not (x in seen or seen.add(x))]


class DeckAnalyzer:
    def __init__(self, llm: LLMClient, settings: Settings, progress: Progress | None = None):
        self.llm = llm
        self.settings = settings
        self.progress = progress or _noop
        self.calls = 0

    # -- public -----------------------------------------------------------

    def analyze(self, extraction: DeckExtraction, options: AnalysisOptions) -> DeckAnalysis:
        included = options.included_slides(extraction)
        if not included:
            raise AnalysisError("There are no slides to analyze (all slides are hidden). Enable 'Include hidden slides'.")
        numbers = [s.number for s in included]
        self.by_num = {s.number: s for s in included}
        self.included_set = set(numbers)
        self.options = options
        self.extraction = extraction
        self.image_slides = {
            s.number
            for s in included
            if s.thumbnail_png and (options.send_all_images or s.needs_vision or s.has_visual_content)
        }
        self.deck_tokens = deck_number_tokens(included, options.include_notes)
        full = deck_text(extraction, included, options, self.image_slides)
        self.full_text_used = len(full) <= self.settings.max_deck_text_chars
        self.full_text = full
        self.outline = outline_text(included)

        batches = _chunks(numbers, self.settings.batch_size)
        reviews: dict[int, SlideReview] = {}
        steps = len(batches) + 1
        for i, batch in enumerate(batches):
            self.progress(
                f"Reviewing slides {batch[0]}–{batch[-1]} (batch {i + 1} of {len(batches)})…", i / steps
            )
            reviews.update(self._review_batch(batch))

        ordered = [reviews[n] for n in numbers]
        self.progress("Checking the deck-wide narrative…", len(batches) / steps)
        analysis = DeckAnalysis(
            deck_filename=extraction.filename,
            total_slides=extraction.total_slides,
            included_slides=numbers,
            extraction_limitations=collect_limitations(
                extraction, included, options, self.image_slides, self.full_text_used
            ),
            slide_reviews=ordered,
            model=self.llm.model_name,
            options=options,
        )
        self._consistency_pass(analysis)
        validate_coverage(analysis, numbers)
        self.progress("Analysis complete.", 1.0)
        return analysis

    # -- stage 1: per-slide reviews ---------------------------------------

    def _context_blocks(self, batch: list[int]) -> list[dict[str, Any]]:
        if self.full_text_used:
            # Identical across batches and the consistency pass -> cacheable prefix.
            return [{"type": "text", "text": self.full_text, "cache_control": {"type": "ephemeral"}}]
        slides = [self.by_num[n] for n in batch]
        partial = deck_text(self.extraction, slides, self.options, self.image_slides)
        return [{"type": "text", "text": self.outline}, {"type": "text", "text": partial}]

    def _review_batch(self, batch: list[int]) -> dict[int, SlideReview]:
        max_attempts = self.settings.validation_retries + 1
        pending = list(batch)
        done: dict[int, SlideReview] = {}
        feedback: list[str] = []
        attempt = 0
        while pending and attempt < max_attempts:
            attempt += 1
            final = attempt == max_attempts
            content = self._context_blocks(pending)
            for n in pending:
                if n in self.image_slides:
                    content.append({"type": "text", "text": f"Image of slide {n}:"})
                    content.append(_image_block(self.by_num[n].thumbnail_png))  # type: ignore[arg-type]
            content.append({"type": "text", "text": batch_task(pending, feedback)})
            try:
                self.calls += 1
                result = self.llm.generate_json(
                    system=SYSTEM_PROMPT, content=content, schema=BATCH_SCHEMA, max_tokens=BATCH_MAX_TOKENS
                )
                parsed: RawBatch = parse_json_model(result.text, RawBatch)
            except MalformedResponseError as exc:
                feedback = [exc.user_message + " Keep each field concise."]
                log.warning("Batch attempt %d: truncated response", attempt)
                continue
            except ValueError as exc:
                feedback = [str(exc)]
                log.warning("Batch attempt %d: invalid structure", attempt)
                continue

            feedback = []
            seen: set[int] = set()
            for raw in parsed.reviews:
                n = raw.slide_number
                if n not in pending:
                    feedback.append(f"Slide {n} was not requested in this batch; do not include it.")
                    continue
                if n in seen:
                    feedback.append(f"Slide {n} was reviewed more than once; return exactly one review.")
                    continue
                seen.add(n)
                hard, soft = check_review(raw, self.by_num[n], self.included_set, self.deck_tokens, self.image_slides)
                if hard:
                    structural = [h for h in hard if "figure(s)" not in h]
                    if not final or structural:
                        feedback.extend(hard)
                        continue
                    soft = soft + [
                        "UNVERIFIED FIGURE: " + h.split(": ", 1)[1] for h in hard
                    ]
                try:
                    done[n] = to_slide_review(raw, self.by_num[n], soft)
                except Exception as exc:  # pydantic validation of enums etc.
                    feedback.append(f"Slide {n}: invalid field values ({str(exc)[:200]}).")
            missing = [n for n in pending if n not in done]
            if missing:
                feedback.append(f"Reviews are missing or invalid for slides {missing}.")
            pending = missing

        if pending:
            raise AnalysisError(
                f"The model did not return valid reviews for slides {', '.join(map(str, pending))} after "
                f"{max_attempts} attempt(s). Last problems: {' '.join(feedback)[:600]} "
                "Try Analyze again; if it keeps failing, lower ANALYSIS_BATCH_SIZE or correct the slide's "
                "extracted title."
            )
        return done

    # -- stage 2: narrative consistency -----------------------------------

    def _consistency_pass(self, analysis: DeckAnalysis) -> None:
        payload = json.dumps(
            [
                {
                    "slide_number": r.slide_number,
                    "section": r.section,
                    "current_title": r.current_title,
                    "recommended_headline": r.recommended_headline,
                    "key_fact": r.key_fact,
                    "significance": r.significance,
                    "evidence_status": r.evidence_status,
                    "claim_types": r.claim_types,
                    "supporting_slides": r.supporting_slides,
                    "evidence_gap": r.evidence_gap,
                    "conflict_note": r.conflict_note,
                    "improvement_priority": r.improvement_priority,
                }
                for r in analysis.slide_reviews
            ],
            ensure_ascii=False,
            indent=1,
        )
        if self.full_text_used:
            context = [{"type": "text", "text": self.full_text, "cache_control": {"type": "ephemeral"}}]
        else:
            context = [{"type": "text", "text": self.outline}]
        max_attempts = self.settings.validation_retries + 1
        feedback: list[str] = []
        parsed: RawConsistency | None = None
        for _attempt in range(max_attempts):
            content = context + [{"type": "text", "text": consistency_task(payload, feedback)}]
            try:
                self.calls += 1
                result = self.llm.generate_json(
                    system=SYSTEM_PROMPT, content=content, schema=CONSISTENCY_SCHEMA, max_tokens=CONSISTENCY_MAX_TOKENS
                )
                parsed = parse_json_model(result.text, RawConsistency)
                break
            except MalformedResponseError as exc:
                feedback = [exc.user_message]
            except ValueError as exc:
                feedback = [str(exc)]

        if parsed is None:
            analysis.analysis_warnings.append(
                "The narrative consistency pass failed validation, so headlines come from the per-slide "
                "review only and priority improvements were derived automatically."
            )
            analysis.priority_improvements = _fallback_priorities(analysis)
            return

        analysis.company_name = parsed.company_name.strip() or None
        analysis.narrative_assessment = " ".join(parsed.narrative_assessment.split())
        analysis.narrative_gaps = [g.strip() for g in parsed.narrative_gaps if g.strip()]
        analysis.appendix_summary = parsed.appendix_summary.strip()

        by_num = {r.slide_number: r for r in analysis.slide_reviews}
        for rev in parsed.headline_revisions:
            review = by_num.get(rev.slide_number)
            text = " ".join(rev.revised_headline.split())
            if review is None or not text:
                analysis.analysis_warnings.append(
                    f"Ignored a narrative revision for slide {rev.slide_number} (not an included slide or empty)."
                )
                continue
            missing = unsupported_numbers(text, self.deck_tokens)
            if missing:
                analysis.analysis_warnings.append(
                    f"Rejected a narrative revision for slide {rev.slide_number} because it introduced "
                    f"figure(s) {missing} not found in the deck."
                )
                continue
            if text != review.recommended_headline:
                review.recommended_headline = text
                review.narrative_revision_reason = rev.reason.strip() or "Revised for narrative consistency."

        priorities: list[PriorityImprovement] = []
        for item in parsed.priority_improvements:
            refs = sorted({r for r in item.slide_refs if r in self.included_set})
            if not item.title.strip():
                continue
            priorities.append(
                PriorityImprovement(rank=len(priorities) + 1, title=item.title.strip(), detail=item.detail.strip(), slide_refs=refs)
            )
            if len(priorities) == 5:
                break
        if len(parsed.priority_improvements) > 5:
            analysis.analysis_warnings.append("More than five priority improvements were returned; the top five were kept.")
        analysis.priority_improvements = priorities


def _fallback_priorities(analysis: DeckAnalysis) -> list[PriorityImprovement]:
    order = {"high": 0, "medium": 1, "low": 2}
    ranked = sorted(analysis.main_reviews, key=lambda r: (order[r.improvement_priority], r.slide_number))
    out = []
    for r in ranked[:5]:
        if r.improvement_priority == "low":
            break
        out.append(
            PriorityImprovement(
                rank=len(out) + 1,
                title=f"Retitle slide {r.slide_number}",
                detail=f"Replace “{r.current_title}” with a headline stating the takeaway."
                + (f" Evidence gap: {r.evidence_gap}" if r.evidence_gap else ""),
                slide_refs=[r.slide_number],
            )
        )
    return out


def analyze_deck(
    extraction: DeckExtraction,
    options: AnalysisOptions,
    llm: LLMClient,
    settings: Settings,
    progress: Progress | None = None,
) -> DeckAnalysis:
    return DeckAnalyzer(llm, settings, progress).analyze(extraction, options)
