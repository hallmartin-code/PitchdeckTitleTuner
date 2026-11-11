"""Automated results email via Resend.

After each new analysis the results are emailed automatically: a summary that
follows the report template (header, narrative assessment, recommended
headlines, priority improvements, gaps, limitations) with the one-page PDF, the
HTML full review, and the JSON analysis attached.

* Recipients come only from configuration (default Info@tencapital.group), never
  from the UI or the uploaded deck.
* On when RESEND_API_KEY is set (NOTIFY_ENABLED=false turns it off).
* Sending never raises and never fails an analysis; the outcome is returned.
* The API key travels only in the Authorization header and is never logged.
* A retried request cannot deliver a second copy (Resend idempotency key).

Uses Resend's REST API through the standard library, so tests can swap the transport.
"""

from __future__ import annotations

import base64
import json
import logging
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from html import escape
from typing import Any

from .config import Settings
from .models import EVIDENCE_STATUS_LABELS, DeckAnalysis
from .report_template import ReportTemplate, load_template

log = logging.getLogger(__name__)

PROVIDER = "Resend"
RESEND_EMAILS_URL = "https://api.resend.com/emails"
#: Resend sits behind Cloudflare, which rejects the default Python-urllib user agent.
USER_AGENT = "ten-capital-headline-tuner/1.0"
MAX_ATTACHMENT_BYTES = 20 * 1024 * 1024
MAX_RETRIES = 2

#: (method, url, headers, body, timeout) -> (status, response body)
Transport = Callable[[str, str, dict[str, str], bytes | None, float], tuple[int, bytes]]


@dataclass
class EmailOutcome:
    to: list[str]
    sender: str
    generation: str
    attempted: bool = False
    sent: bool = False
    subject: str = ""
    attachments: list[str] = field(default_factory=list)
    message_id: str | None = None
    error: str | None = None
    skipped_reason: str | None = None
    at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds"))

    @property
    def status_text(self) -> str:
        if self.sent:
            return f"Results emailed to {', '.join(self.to)}"
        if self.skipped_reason:
            return f"Results email not sent: {self.skipped_reason}"
        return f"Results email failed: {self.error}"


def urllib_transport(method: str, url: str, headers: dict[str, str], body: bytes | None,
                     timeout: float) -> tuple[int, bytes]:
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - fixed https URL
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


# ---------------------------------------------------------------------------
# Rendering (follows the report template's sections and labels)
# ---------------------------------------------------------------------------


def _date(d: datetime) -> str:
    return d.strftime("%d %B %Y").lstrip("0")


def render_email(
    analysis: DeckAnalysis,
    *,
    subject_prefix: str,
    attachment_names: list[str],
    app_url: str | None = None,
    template: ReportTemplate | None = None,
) -> tuple[str, str, str]:
    """Return (subject, html, text)."""
    tpl = template or load_template()
    c = tpl.palette
    sec = {s.id: s for s in tpl.sections}
    company = analysis.company_name or tpl.header.title_fallback
    date_text = _date(analysis.generated_at)
    attention = sum(1 for r in analysis.slide_reviews if r.needs_attention)
    subject = (
        f"{subject_prefix} {tpl.header.title_fallback} — {company} · "
        f"{len(analysis.slide_reviews)} slides reviewed · {attention} need attention"
    ).strip()

    cell = f"padding:6px 8px;border-bottom:1px solid {c.rule};vertical-align:top;font-size:13px;"
    head = f"padding:6px 8px;background:{c.primary};color:#FFFFFF;text-align:left;font-size:12px;"

    def h(title: str) -> str:
        return (f'<h3 style="color:{c.primary};font-size:15px;margin:22px 0 6px;border-bottom:2px solid {c.accent};'
                f'padding-bottom:3px">{escape(title)}</h3>')

    html = [
        f'<div style="font-family:Arial,Helvetica,sans-serif;color:#1F2328;max-width:860px">',
        f'<p style="color:{c.accent};font-size:11px;font-weight:bold;letter-spacing:.08em;margin:0">'
        f'{escape(tpl.header.eyebrow)}</p>',
        f'<h2 style="color:{c.primary};margin:2px 0 4px;font-size:21px">{escape(company)}</h2>',
        f'<p style="color:{c.muted_text};font-size:12px;margin:0">Source: {escape(analysis.deck_filename)} · '
        f'{analysis.total_slides} slides ({len(analysis.included_slides)} reviewed) · Reviewed {escape(date_text)} · '
        f'Model: {escape(analysis.model)}</p>',
    ]
    text = [
        f"{tpl.header.title_fallback.upper()} — {company}",
        f"Source: {analysis.deck_filename} · {analysis.total_slides} slides ({len(analysis.included_slides)} reviewed)"
        f" · Reviewed {date_text} · Model: {analysis.model}",
    ]

    narrative = sec.get("narrative_assessment")
    if narrative and narrative.enabled and analysis.narrative_assessment:
        html += [h(narrative.heading), f"<p>{escape(analysis.narrative_assessment)}</p>"]
        text += ["", narrative.heading.upper(), analysis.narrative_assessment]

    table = sec["headline_table"]
    flag = table.flag.symbol if table.flag else "†"
    html.append(h(table.heading))
    html.append('<table style="border-collapse:collapse;width:100%"><tr>'
                + "".join(f'<th style="{head}">{escape(col.header)}</th>' for col in table.columns)
                + f'<th style="{head}">Evidence</th></tr>')
    text += ["", table.heading.upper()]
    for r in analysis.main_reviews:
        status = EVIDENCE_STATUS_LABELS[r.evidence_status]
        mark = f" {flag}" if r.evidence_status != "supported" else ""
        html.append(
            f'<tr><td style="{cell}"><b>{r.slide_number}</b></td><td style="{cell}">{escape(r.current_title)}</td>'
            f'<td style="{cell}color:{c.primary}"><b>{escape(r.recommended_headline)}</b>{escape(mark)}</td>'
            f'<td style="{cell}">{escape(status)}</td></tr>'
        )
        text.append(f"  {r.slide_number}. {r.recommended_headline}{mark}   [was: {r.current_title}; {status}]")
    html.append("</table>")
    if any(r.evidence_status != "supported" for r in analysis.main_reviews) and table.flag:
        legend = table.flag.legend.replace("{symbol}", flag)
        html.append(f'<p style="color:{c.muted_text};font-size:12px">{escape(legend)}</p>')
        text.append(f"  {legend}")
    if analysis.appendix_reviews:
        html.append(f'<p style="color:{c.muted_text};font-size:12px">Appendix slides '
                    f'({len(analysis.appendix_reviews)}) are reviewed in the attached full review.</p>')

    prio = sec.get("priority_improvements")
    if prio and prio.enabled and analysis.priority_improvements:
        html += [h(prio.heading), "<ol>"]
        text += ["", prio.heading.upper()]
        for p in analysis.priority_improvements[: prio.max_items]:
            refs = ""
            if p.slide_refs:
                label = prio.slide_ref_label_singular if len(p.slide_refs) == 1 else prio.slide_ref_label_plural
                refs = f" ({label} {', '.join(map(str, p.slide_refs))})"
            html.append(f"<li><b>{escape(p.title)}</b>{escape(prio.title_detail_separator)}{escape(p.detail)}"
                        f'<span style="color:{c.muted_text}">{escape(refs)}</span></li>')
            text.append(f"  {p.rank}. {p.title}{prio.title_detail_separator}{p.detail}{refs}")
        html.append("</ol>")

    if analysis.narrative_gaps:
        html += [h("Narrative gaps"), "<ul>" + "".join(f"<li>{escape(g)}</li>" for g in analysis.narrative_gaps) + "</ul>"]
        text += ["", "NARRATIVE GAPS"] + [f"  - {g}" for g in analysis.narrative_gaps]
    warnings = analysis.analysis_warnings + analysis.extraction_limitations
    if warnings:
        html += [h("Extraction limitations and warnings"),
                 "<ul>" + "".join(f"<li>{escape(w)}</li>" for w in warnings) + "</ul>"]
        text += ["", "EXTRACTION LIMITATIONS AND WARNINGS"] + [f"  - {w}" for w in warnings]

    footer = tpl.footer.text.replace("{source_filename}", analysis.deck_filename)
    link = f' Open the app: <a href="{escape(app_url)}">{escape(app_url)}</a>.' if app_url else ""
    html.append(
        f'<p style="color:{c.muted_text};font-size:12px;margin-top:22px">Attached: {escape(", ".join(attachment_names))}.'
        f"{link}</p><p style=\"color:{c.muted_text};font-size:12px\">{escape(footer)}</p></div>"
    )
    text += ["", f"Attached: {', '.join(attachment_names)}." + (f" App: {app_url}" if app_url else ""), footer]
    return subject, "".join(html), "\n".join(text)


# ---------------------------------------------------------------------------
# Sending
# ---------------------------------------------------------------------------


def _json(raw: bytes) -> dict[str, Any]:
    try:
        data = json.loads(raw.decode("utf-8") or "{}")
        return data if isinstance(data, dict) else {}
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {}


def _error_text(status: int, raw: bytes) -> str:
    detail = str(_json(raw).get("message") or raw[:200].decode("utf-8", "replace")).strip()
    if status == 401:
        return "Resend rejected the API key (401). Check RESEND_API_KEY."
    if status == 403:
        return f"Resend refused the request (403): {detail}. The sender domain must be verified in Resend."
    if status == 422:
        return f"Resend rejected the message (422): {detail}."
    if status == 429:
        return "Resend rate limit reached (429)."
    return f"Resend returned {status}: {detail[:200]}"


def send_results_email(
    analysis: DeckAnalysis,
    files: dict[str, bytes],
    *,
    settings: Settings,
    generation: str,
    transport: Transport | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> EmailOutcome:
    """Email the results with the given attachments ({filename: bytes}). Never raises."""
    out = EmailOutcome(list(settings.notify_to), settings.notify_from, generation)
    key = (settings.resend_api_key or "").strip()
    if not settings.notify_enabled:
        out.skipped_reason = "email notifications are disabled (NOTIFY_ENABLED=false)"
        return out
    if not key:
        out.skipped_reason = "no RESEND_API_KEY configured"
        return out
    if not out.to:
        out.skipped_reason = "no recipients configured (NOTIFY_TO)"
        return out

    attachments, total, omitted = [], 0, []
    for name, data in files.items():
        if total + len(data) > MAX_ATTACHMENT_BYTES:
            omitted.append(name)
            continue
        attachments.append((name, data))
        total += len(data)
    try:
        subject, html_body, text_body = render_email(
            analysis, subject_prefix=settings.notify_subject_prefix,
            attachment_names=[n for n, _ in attachments], app_url=settings.public_url,
        )
    except Exception as exc:  # noqa: BLE001 - a rendering bug must not fail the analysis
        log.error("Results email could not be rendered: %s", type(exc).__name__)
        out.error = f"email could not be rendered ({type(exc).__name__})"
        return out
    if omitted:
        text_body += f"\n\nNot attached (size limit): {', '.join(omitted)}. Download them from the app."
        html_body += f'<p style="color:#8C1D18;font-size:12px">Not attached (size limit): {escape(", ".join(omitted))}.</p>'

    payload: dict[str, Any] = {
        "from": settings.notify_from,
        "to": out.to,
        "subject": subject,
        "html": html_body,
        "text": text_body,
        "tags": [{"name": "app", "value": "headline-tuner"}, {"name": "generation", "value": generation[:64]}],
        "attachments": [{"filename": n, "content": base64.b64encode(d).decode("ascii")} for n, d in attachments],
    }
    if settings.notify_reply_to:
        payload["reply_to"] = list(settings.notify_reply_to)
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "User-Agent": USER_AGENT,
        "Idempotency-Key": f"headline-tuner/{generation[:200]}",
    }
    body = json.dumps(payload).encode("utf-8")
    out.attempted, out.subject, out.attachments = True, subject, [n for n, _ in attachments]
    send = transport or urllib_transport
    last_error = "unknown error"
    for attempt in range(MAX_RETRIES + 1):
        try:
            status, raw = send("POST", RESEND_EMAILS_URL, headers, body, settings.notify_timeout_s)
        except Exception as exc:  # noqa: BLE001 - network failures must not fail an analysis
            last_error = f"could not reach Resend ({type(exc).__name__})"
        else:
            if 200 <= status < 300:
                out.sent, out.message_id = True, _json(raw).get("id")
                log.info("Results email sent to %d recipient(s)", len(out.to))
                return out
            last_error = _error_text(status, raw)
            if status != 429 and 400 <= status < 500:
                break  # a client error will not succeed on retry
        if attempt < MAX_RETRIES:
            sleep(1.5 * (attempt + 1))
    out.error = last_error
    log.warning("Results email not sent: %s", last_error)
    return out
