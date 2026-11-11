"""Pydantic data models for extraction and analysis."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

NO_TITLE = "No identifiable title."

Section = Literal["main", "appendix"]
TitleConfidence = Literal["high", "medium", "low", "none"]
EvidenceStatus = Literal[
    "supported", "intended_benefit", "insufficient_evidence", "conflicting_evidence"
]
EvidenceSource = Literal["slide_text", "visual", "speaker_notes", "hidden_slide", "mixed"]
ClaimType = Literal[
    "actual_result",
    "forecast",
    "target",
    "pilot",
    "signed_contract",
    "expression_of_interest",
    "company_claim",
    "none",
]
SignificanceType = Literal["customer_benefit", "investor_significance"]
Priority = Literal["high", "medium", "low"]

EVIDENCE_STATUS_LABELS = {
    "supported": "Supported",
    "intended_benefit": "Intended benefit",
    "insufficient_evidence": "Insufficient evidence",
    "conflicting_evidence": "Conflicting evidence",
}

CLAIM_TYPE_LABELS = {
    "actual_result": "Actual result",
    "forecast": "Forecast",
    "target": "Target",
    "pilot": "Pilot",
    "signed_contract": "Signed contract",
    "expression_of_interest": "Expression of interest",
    "company_claim": "Company claim",
    "none": "No quantified claim",
}


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------


class SlideContent(BaseModel):
    """Everything extracted from a single slide. Slide numbers are one-based."""

    model_config = ConfigDict(validate_assignment=True)

    number: int = Field(ge=1)
    title: str | None = None
    title_confidence: TitleConfidence = "none"
    title_source: str = ""
    title_edited: bool = False
    text: list[str] = Field(default_factory=list)
    tables: list[list[list[str]]] = Field(default_factory=list)
    chart_data: list[str] = Field(default_factory=list)
    numeric_labels: list[str] = Field(default_factory=list)
    notes: str = ""
    ocr_text: str = ""
    hidden: bool = False
    image_count: int = 0
    has_visual_content: bool = False
    needs_vision: bool = False
    warnings: list[str] = Field(default_factory=list)
    section: Section = "main"
    # Rendered slide image (PNG). Excluded from JSON exports and API payload text.
    thumbnail_png: bytes | None = Field(default=None, exclude=True, repr=False)

    @property
    def display_title(self) -> str:
        return self.title.strip() if self.title and self.title.strip() else NO_TITLE

    @property
    def title_uncertain(self) -> bool:
        return not self.title_edited and self.title_confidence in {"low", "none"}

    @property
    def text_char_count(self) -> int:
        return sum(len(t) for t in self.text) + sum(
            len(c) for table in self.tables for row in table for c in row
        )


class DeckExtraction(BaseModel):
    filename: str
    file_format: Literal["pdf", "pptx", "ppt"]
    source_sha256: str
    slides: list[SlideContent]
    warnings: list[str] = Field(default_factory=list)
    rendering_available: bool = False

    @property
    def total_slides(self) -> int:
        return len(self.slides)

    def slide(self, number: int) -> SlideContent:
        return self.slides[number - 1]

    def apply_appendix_start(self, appendix_start: int | None) -> None:
        """Mark slides from ``appendix_start`` (one-based) onward as appendix."""
        for s in self.slides:
            s.section = (
                "appendix" if appendix_start is not None and s.number >= appendix_start else "main"
            )

    def suggest_appendix_start(self) -> int | None:
        for s in self.slides:
            if s.title and s.title.strip().lower().startswith("appendix"):
                return s.number
        return None


class AnalysisOptions(BaseModel):
    include_notes: bool = False
    include_hidden: bool = False
    send_all_images: bool = False

    def included_slides(self, extraction: DeckExtraction) -> list[SlideContent]:
        return [s for s in extraction.slides if self.include_hidden or not s.hidden]


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------


class SlideReview(BaseModel):
    slide_number: int = Field(ge=1)
    current_title: str
    recommended_headline: str
    key_fact: str = Field(description="Feature, capability, or key fact")
    significance: str = Field(description="Customer benefit or investor significance")
    significance_type: SignificanceType
    supporting_slides: list[int]
    cross_slide_support: bool = False
    evidence_excerpt: str
    evidence_source: EvidenceSource = "slide_text"
    evidence_status: EvidenceStatus
    claim_types: list[ClaimType] = Field(default_factory=list)
    evidence_gap: str = ""
    conflict_note: str = ""
    improvement_priority: Priority = "medium"
    section: Section = "main"
    narrative_revision_reason: str = ""
    validation_warnings: list[str] = Field(default_factory=list)

    @field_validator("recommended_headline", "evidence_excerpt", "key_fact", "significance")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        v = " ".join(v.split())
        if not v:
            raise ValueError("must not be empty")
        return v

    @property
    def word_count(self) -> int:
        return len(self.recommended_headline.split())

    @property
    def needs_attention(self) -> bool:
        return self.evidence_status != "supported" or bool(self.validation_warnings)


class PriorityImprovement(BaseModel):
    rank: int = Field(ge=1, le=5)
    title: str
    detail: str
    slide_refs: list[int] = Field(default_factory=list)


class NarrativeLine(BaseModel):
    slide_number: int
    headline: str


class DeckAnalysis(BaseModel):
    company_name: str | None = None
    deck_filename: str
    total_slides: int
    included_slides: list[int]
    extraction_limitations: list[str] = Field(default_factory=list)
    narrative_assessment: str = ""
    slide_reviews: list[SlideReview]
    narrative_gaps: list[str] = Field(default_factory=list)
    appendix_summary: str = ""
    priority_improvements: list[PriorityImprovement] = Field(default_factory=list, max_length=5)
    analysis_warnings: list[str] = Field(default_factory=list)
    model: str = ""
    options: AnalysisOptions = Field(default_factory=AnalysisOptions)
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def main_reviews(self) -> list[SlideReview]:
        return [r for r in self.slide_reviews if r.section == "main"]

    @property
    def appendix_reviews(self) -> list[SlideReview]:
        return [r for r in self.slide_reviews if r.section == "appendix"]

    @property
    def main_narrative(self) -> list[NarrativeLine]:
        """Headlines-only narrative for the main deck, in slide order."""
        return [
            NarrativeLine(slide_number=r.slide_number, headline=r.recommended_headline)
            for r in sorted(self.main_reviews, key=lambda r: r.slide_number)
        ]

    def review(self, slide_number: int) -> SlideReview:
        for r in self.slide_reviews:
            if r.slide_number == slide_number:
                return r
        raise KeyError(slide_number)
