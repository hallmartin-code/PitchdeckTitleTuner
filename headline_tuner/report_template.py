"""Loads and validates the one-page review template (templates/*.json).

The template defines the document's structure, wording, styling, and the
analysis fields every review must contain. Safety limits from the renderer
(one page, table text at least 9 pt) cannot be relaxed by a template.
"""

from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .config import PROJECT_ROOT
from .errors import ConfigurationError

DEFAULT_TEMPLATE = PROJECT_ROOT / "templates" / "headline_review_template.json"
HARD_MIN_TABLE_PT = 9.0
KNOWN_SECTIONS = {"narrative_assessment", "headline_table", "priority_improvements"}


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Margins(_Strict):
    left: float
    right: float
    top: float
    bottom: float


class TopBar(_Strict):
    height_in: float = 0.16
    accent_height_pt: float = 2.5


class Page(_Strict):
    size: Literal["letter"] = "letter"
    orientation: Literal["portrait"] = "portrait"
    max_pages: Literal[1] = 1
    margins_in: Margins
    top_bar: TopBar = Field(default_factory=TopBar)


class Palette(_Strict):
    primary: str
    accent: str
    row_tint: str
    muted_text: str
    rule: str

    @field_validator("*")
    @classmethod
    def _hex(cls, v: str) -> str:
        if not (len(v) == 7 and v.startswith("#") and all(c in "0123456789abcdefABCDEF" for c in v[1:])):
            raise ValueError("colors must be #RRGGBB")
        return v


class Typography(_Strict):
    font_family: str = ""
    title_pt: float
    eyebrow_pt: float
    meta_pt: float
    section_heading_pt: float
    body_pt: float
    note_pt: float
    legend_pt: float
    footer_pt: float
    table_pt_steps: list[float]
    min_table_pt: float

    @model_validator(mode="after")
    def _minimums(self) -> "Typography":
        if self.min_table_pt < HARD_MIN_TABLE_PT:
            raise ValueError(f"min_table_pt may not be below {HARD_MIN_TABLE_PT}")
        if not self.table_pt_steps or any(s < self.min_table_pt for s in self.table_pt_steps):
            raise ValueError("table_pt_steps must be non-empty and not below min_table_pt")
        self.table_pt_steps = sorted(set(self.table_pt_steps), reverse=True)
        return self


class Breakdown(_Strict):
    main: str
    appendix: str
    excluded: str


class Header(_Strict):
    eyebrow: str
    title_field: Literal["company_name"] = "company_name"
    title_fallback: str
    meta_separator: str
    meta_items: list[str]
    slide_count_breakdown: Breakdown


class Column(_Strict):
    header: str
    field: Literal["slide_number", "current_title", "recommended_headline"]
    width_in: float | None
    style: Literal["number", "text", "headline"]


class Flag(_Strict):
    symbol: str
    applies_when: str = ""
    legend: str


class Section(_Strict):
    id: str
    enabled: bool = True
    heading: str
    field: str | None = None
    omit_if_empty: bool = True
    guidance: str = ""
    # headline_table
    coverage_note: str | None = None
    appendix_note: str | None = None
    columns: list[Column] | None = None
    rows: str = ""
    flag: Flag | None = None
    # priority_improvements
    max_items: int = Field(default=5, ge=1, le=5)
    title_detail_separator: str = " — "
    slide_ref_label_singular: str = "Slide"
    slide_ref_label_plural: str = "Slides"

    @model_validator(mode="after")
    def _shape(self) -> "Section":
        if self.id not in KNOWN_SECTIONS:
            raise ValueError(f"unknown section id {self.id!r}; expected one of {sorted(KNOWN_SECTIONS)}")
        if self.id == "headline_table":
            if not self.columns or not self.coverage_note or not self.flag:
                raise ValueError("headline_table needs columns, coverage_note, and flag")
            if "{shown}" not in self.coverage_note or "{total}" not in self.coverage_note:
                raise ValueError("coverage_note must contain {shown} and {total}")
            if sum(1 for c in self.columns if c.width_in is None) != 1:
                raise ValueError("exactly one table column must have width_in null (it takes the remaining width)")
        return self


class Footer(_Strict):
    text: str
    prepared_by: str
    generated: str


class FieldSpec(_Strict):
    name: str
    label: str
    description: str = ""
    allowed_values: list[str] | None = None


class AnalysisFields(_Strict):
    deck: list[FieldSpec]
    slide_review: list[FieldSpec]
    priority_improvement: list[FieldSpec]


class ReportTemplate(_Strict):
    template_name: str
    template_version: int
    description: str = ""
    page: Page
    palette: Palette
    typography: Typography
    header: Header
    sections: list[Section]
    footer: Footer
    analysis_fields: AnalysisFields

    @model_validator(mode="after")
    def _sections(self) -> "ReportTemplate":
        ids = [s.id for s in self.sections]
        if len(ids) != len(set(ids)):
            raise ValueError("section ids must be unique")
        if "headline_table" not in ids:
            raise ValueError("the template must include the headline_table section")
        return self

    def section(self, section_id: str) -> Section | None:
        return next((s for s in self.sections if s.id == section_id), None)


def load_template(path: str | Path | None = None) -> ReportTemplate:
    """Load a template from ``path``, $REPORT_TEMPLATE_PATH, or the bundled default."""
    resolved = Path(path or os.environ.get("REPORT_TEMPLATE_PATH") or DEFAULT_TEMPLATE)
    return _load(str(resolved.resolve()), resolved.stat().st_mtime if resolved.exists() else 0.0)


@lru_cache(maxsize=8)
def _load(path: str, _mtime: float) -> ReportTemplate:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigurationError(f"Report template not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ConfigurationError(f"Report template is not valid JSON ({exc.msg}, line {exc.lineno}).") from exc
    try:
        return ReportTemplate.model_validate(data)
    except Exception as exc:
        raise ConfigurationError(f"Report template is invalid: {str(exc)[:500]}") from exc
