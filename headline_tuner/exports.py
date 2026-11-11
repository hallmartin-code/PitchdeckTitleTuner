"""Full-review exports: JSON (machine-readable) and self-contained HTML."""

from __future__ import annotations

import json
from html import escape

from .models import CLAIM_TYPE_LABELS, EVIDENCE_STATUS_LABELS, DeckAnalysis, DeckExtraction, SlideReview


def to_json(analysis: DeckAnalysis, extraction: DeckExtraction | None = None) -> str:
    data = {
        "analysis": json.loads(analysis.model_dump_json()),
        "main_deck_narrative": [l.model_dump() for l in analysis.main_narrative],
    }
    if extraction is not None:
        data["extraction"] = json.loads(extraction.model_dump_json())  # images are excluded
    return json.dumps(data, ensure_ascii=False, indent=2)


def _e(s: object) -> str:
    return escape(str(s), quote=True)


_CSS = """
:root{--navy:#1B2A4A;--teal:#0E7C72;--tint:#ECF6F4;--grey:#5A6472;--rule:#CAD4DD;--warn:#9A5B00;--warnbg:#FFF6E5}
*{box-sizing:border-box}body{margin:0;background:#fff;color:#1d232b;font:15px/1.5 -apple-system,"Segoe UI",Roboto,"DejaVu Sans",Arial,sans-serif}
main{max-width:1080px;margin:0 auto;padding:32px 20px 64px}
.eyebrow{color:var(--teal);font-weight:700;letter-spacing:.06em;font-size:12px;text-transform:uppercase;margin:0}
h1{color:var(--navy);margin:2px 0 4px;font-size:28px}h2{color:var(--navy);font-size:19px;margin:32px 0 8px;border-bottom:2px solid var(--teal);padding-bottom:4px}
.meta{color:var(--grey);font-size:13px}
table{border-collapse:collapse;width:100%;font-size:14px}th{background:var(--navy);color:#fff;text-align:left;padding:7px 8px;vertical-align:top}
td{padding:7px 8px;border-bottom:1px solid var(--rule);vertical-align:top}tr:nth-child(even) td{background:var(--tint)}
.card{border:1px solid var(--rule);border-radius:8px;padding:14px 16px;margin:12px 0}
.card h3{margin:0 0 6px;color:var(--navy);font-size:16px}.headline{font-weight:700;color:var(--navy)}
.badge{display:inline-block;font-size:12px;padding:1px 8px;border-radius:10px;background:var(--tint);color:var(--teal);margin-right:4px}
.badge.warn{background:var(--warnbg);color:var(--warn)}
.warnbox{background:var(--warnbg);border-left:4px solid var(--warn);padding:8px 12px;margin:8px 0;font-size:14px}
dl{display:grid;grid-template-columns:200px 1fr;gap:4px 12px;margin:8px 0 0}dt{color:var(--grey)}dd{margin:0}
ol.narrative li{margin:4px 0}footer{color:var(--grey);font-size:12px;margin-top:40px;border-top:1px solid var(--rule);padding-top:10px}
@media (max-width:640px){dl{grid-template-columns:1fr}th:nth-child(2),td:nth-child(2){display:none}}
"""


def _review_card(r: SlideReview) -> str:
    status_cls = "" if r.evidence_status == "supported" else " warn"
    claims = "".join(f'<span class="badge">{_e(CLAIM_TYPE_LABELS.get(c, c))}</span>' for c in r.claim_types)
    warn = "".join(f'<div class="warnbox">{_e(w)}</div>' for w in r.validation_warnings)
    rows = [
        ("Current title", r.current_title),
        ("Feature / key fact", r.key_fact),
        ("Customer benefit" if r.significance_type == "customer_benefit" else "Investor significance", r.significance),
        ("Supporting slides", ", ".join(map(str, r.supporting_slides)) + (" (cross-slide)" if r.cross_slide_support else "")),
        ("Evidence", f"“{r.evidence_excerpt}” ({r.evidence_source.replace('_', ' ')})"),
        ("Evidence gap", r.evidence_gap or "—"),
    ]
    if r.conflict_note:
        rows.append(("Conflicting figures", r.conflict_note))
    if r.narrative_revision_reason:
        rows.append(("Narrative revision", r.narrative_revision_reason))
    dl = "".join(f"<dt>{_e(k)}</dt><dd>{_e(v)}</dd>" for k, v in rows)
    return (
        f'<div class="card" id="slide-{r.slide_number}"><h3>Slide {r.slide_number}</h3>'
        f'<div class="headline">{_e(r.recommended_headline)}</div>'
        f'<div><span class="badge{status_cls}">{_e(EVIDENCE_STATUS_LABELS[r.evidence_status])}</span>'
        f'<span class="badge">Priority: {_e(r.improvement_priority)}</span>{claims}</div>'
        f"{warn}<dl>{dl}</dl></div>"
    )


def to_html(analysis: DeckAnalysis) -> str:
    title = analysis.company_name or "Pitch deck"
    parts = [
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>",
        "<meta name='viewport' content='width=device-width,initial-scale=1'>",
        f"<title>{_e(title)} — Headline Review</title><style>{_CSS}</style></head><body><main>",
        "<p class='eyebrow'>Pitch Deck Headline Review — full analysis</p>",
        f"<h1>{_e(title)}</h1>",
        f"<p class='meta'>Source: {_e(analysis.deck_filename)} · {analysis.total_slides} slides "
        f"({len(analysis.included_slides)} reviewed) · Generated {analysis.generated_at:%d %B %Y} · "
        f"Model: {_e(analysis.model)}</p>",
    ]
    if analysis.narrative_assessment:
        parts.append(f"<h2>Narrative assessment</h2><p>{_e(analysis.narrative_assessment)}</p>")
    if analysis.extraction_limitations or analysis.analysis_warnings:
        parts.append("<h2>Extraction limitations and warnings</h2>")
        parts += [f"<div class='warnbox'>{_e(w)}</div>" for w in analysis.extraction_limitations + analysis.analysis_warnings]

    parts.append("<h2>Main-deck narrative (headlines only)</h2><ol class='narrative'>")
    parts += [f"<li value='{l.slide_number}'>{_e(l.headline)}</li>" for l in analysis.main_narrative]
    parts.append("</ol>")

    parts.append("<h2>Summary table</h2><table><thead><tr><th>Slide</th><th>Current title</th>"
                 "<th>Recommended headline</th><th>Evidence</th></tr></thead><tbody>")
    for r in analysis.slide_reviews:
        parts.append(
            f"<tr><td>{r.slide_number}{' (appendix)' if r.section == 'appendix' else ''}</td>"
            f"<td>{_e(r.current_title)}</td><td>{_e(r.recommended_headline)}</td>"
            f"<td>{_e(EVIDENCE_STATUS_LABELS[r.evidence_status])}</td></tr>"
        )
    parts.append("</tbody></table>")

    if analysis.priority_improvements:
        parts.append("<h2>Priority improvements</h2><ol>")
        for p in analysis.priority_improvements:
            refs = f" (slides {', '.join(map(str, p.slide_refs))})" if p.slide_refs else ""
            parts.append(f"<li><strong>{_e(p.title)}</strong> — {_e(p.detail)}{_e(refs)}</li>")
        parts.append("</ol>")
    if analysis.narrative_gaps:
        parts.append("<h2>Narrative gaps</h2><ul>" + "".join(f"<li>{_e(g)}</li>" for g in analysis.narrative_gaps) + "</ul>")

    parts.append("<h2>Slide-by-slide review — main deck</h2>")
    parts += [_review_card(r) for r in analysis.main_reviews]
    if analysis.appendix_reviews:
        parts.append("<h2>Appendix review</h2>")
        if analysis.appendix_summary:
            parts.append(f"<p>{_e(analysis.appendix_summary)}</p>")
        parts += [_review_card(r) for r in analysis.appendix_reviews]
    parts.append(
        "<footer>Findings are based solely on the content of the supplied deck and have not been independently "
        "verified. Company claims are reported as stated. The source file was not modified.</footer>"
    )
    parts.append("</main></body></html>")
    return "".join(parts)
