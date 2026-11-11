import json
import typing

import pymupdf
import pytest

from headline_tuner.errors import ConfigurationError
from headline_tuner.models import DeckAnalysis, PriorityImprovement, SlideReview
from headline_tuner.pdf_report import ReportContent, auto_select, page_count, placeholder_content, render_pdf
from headline_tuner.report_template import DEFAULT_TEMPLATE, load_template
from headline_tuner.synthetic import sample_analysis

# Words that occur only in the synthetic company's deck; none may leak into the template.
COMPANY_SPECIFIC = ["Tidewater", "Marlow", "Harbor", "Dana", "Okafor", "Ruiz", "hydrant", "leak", "utilit", "sensor", "$"]


def _text(pdf: bytes) -> str:
    with pymupdf.open(stream=pdf, filetype="pdf") as doc:
        return " ".join(doc[0].get_text().split())


def _write(tmp_path, mutate):
    data = json.loads(DEFAULT_TEMPLATE.read_text(encoding="utf-8"))
    mutate(data)
    p = tmp_path / "tpl.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    return p


def test_default_template_loads_and_has_no_company_data():
    tpl = load_template()
    assert [s.id for s in tpl.sections] == ["narrative_assessment", "headline_table", "priority_improvements"]
    raw = DEFAULT_TEMPLATE.read_text(encoding="utf-8").lower()
    for word in COMPANY_SPECIFIC:
        assert word.lower() not in raw, word


def _literal_values(model, field):
    ann = model.model_fields[field].annotation
    args = typing.get_args(ann)
    inner = args[0] if args and typing.get_origin(ann) is list else ann
    return set(typing.get_args(inner))


def test_analysis_fields_match_the_data_models():
    f = load_template().analysis_fields
    slide_names = {x.name for x in f.slide_review}
    assert slide_names <= set(SlideReview.model_fields)
    # every substantive review field is defined in the template
    assert set(SlideReview.model_fields) - slide_names <= {"narrative_revision_reason", "validation_warnings"}
    assert {x.name for x in f.deck} <= set(DeckAnalysis.model_fields)
    assert {x.name for x in f.priority_improvement} == set(PriorityImprovement.model_fields)
    for spec in f.slide_review:
        if spec.allowed_values:
            assert set(spec.allowed_values) == _literal_values(SlideReview, spec.name), spec.name


def test_placeholder_preview_is_one_page_structure_only():
    result = render_pdf(placeholder_content(), list(range(1, 9)))
    assert page_count(result.pdf) == 1
    text = _text(result.pdf)
    for part in ["[Company name]", "Narrative assessment", "Recommended headlines", "Priority improvements",
                 "Slide Current title Recommended headline", "[deck filename]", "Findings are based solely"]:
        assert part in text, part
    for word in COMPANY_SPECIFIC:
        assert word.lower() not in text.lower(), word


def test_separator_spacing_preserved():
    content = ReportContent.from_analysis(sample_analysis())
    text = _text(render_pdf(content, auto_select(content)).pdf)
    assert "Resolve the pilot leak-count conflict — Slide 4 cites" in text


def test_custom_template_changes_output(tmp_path, monkeypatch):
    def mutate(d):
        d["header"]["eyebrow"] = "CUSTOM EYEBROW"
        d["sections"][1]["heading"] = "Suggested headlines"
        d["sections"][1]["columns"][1]["header"] = "Existing title"
        d["sections"][0]["enabled"] = False
        d["footer"]["generated"] = "Produced {review_date}."
        d["palette"]["primary"] = "#000000"

    monkeypatch.setenv("REPORT_TEMPLATE_PATH", str(_write(tmp_path, mutate)))
    content = ReportContent.from_analysis(sample_analysis())
    text = _text(render_pdf(content, auto_select(content)).pdf)
    assert "CUSTOM EYEBROW" in text and "Suggested headlines" in text and "Existing title" in text
    assert "Narrative assessment" not in text and "Produced 11 November 2026." in text


@pytest.mark.parametrize(
    "mutate, fragment",
    [
        (lambda d: d["typography"].update(min_table_pt=8), "min_table_pt"),
        (lambda d: d["typography"].update(table_pt_steps=[9.5, 8.5]), "table_pt_steps"),
        (lambda d: d["sections"].append({"id": "mystery", "heading": "x"}), "unknown section"),
        (lambda d: d["sections"].pop(1), "headline_table"),
        (lambda d: d["sections"][1].update(coverage_note="All shown."), "{shown}"),
        (lambda d: d["page"].update(max_pages=2), "max_pages"),
        (lambda d: d["palette"].update(primary="navy"), "#RRGGBB"),
        (lambda d: d["sections"][2].update(max_items=7), "max_items"),
    ],
)
def test_invalid_templates_are_rejected(tmp_path, mutate, fragment):
    with pytest.raises(ConfigurationError) as exc:
        load_template(_write(tmp_path, mutate))
    assert fragment in exc.value.user_message


def test_malformed_template_json(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("{not json", encoding="utf-8")
    with pytest.raises(ConfigurationError, match="not valid JSON"):
        load_template(p)
