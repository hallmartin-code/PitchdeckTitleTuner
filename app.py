"""Pitch Deck Headline Tuner — Streamlit interface.

Run with:  streamlit run app.py
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from datetime import date

import streamlit as st

from headline_tuner.analyzer import analyze_deck
from headline_tuner.config import Settings, get_api_key, has_api_key, load_settings
from headline_tuner.convert import INSTALL_INSTRUCTIONS, find_libreoffice
from headline_tuner.errors import ConfigurationError, HeadlineTunerError, LayoutOverflowError
from headline_tuner.exports import to_html, to_json
from headline_tuner.extraction import extract_deck, ocr_available
from headline_tuner.llm import AnthropicLLM
from headline_tuner.models import (
    CLAIM_TYPE_LABELS,
    EVIDENCE_STATUS_LABELS,
    NO_TITLE,
    AnalysisOptions,
    DeckAnalysis,
    DeckExtraction,
    SlideReview,
)
from headline_tuner import ui
from headline_tuner.notify import EmailOutcome, send_results_email
from headline_tuner.pdf_report import ReportContent, auto_select, preview_png, render_pdf

# Log operational events only — never deck content or credentials.
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("headline_tuner.app")

FAVICON = Path(__file__).resolve().parent / "static" / "favicon.png"
st.set_page_config(
    page_title="Pitch Deck Headline Review · TEN Capital Network",
    page_icon=str(FAVICON) if FAVICON.is_file() else None,
    layout="wide",
)
ui.inject_theme()


def _secrets():
    try:
        st.secrets.get("ANTHROPIC_API_KEY")  # raises if no secrets file exists
        return st.secrets
    except Exception:
        return None


SECRETS = _secrets()
try:
    SETTINGS: Settings = load_settings(SECRETS)
except ConfigurationError as exc:
    st.error(exc.user_message)
    st.stop()

ss = st.session_state
ss.setdefault("analysis_cache", {})
ss.setdefault("emails", {})  # generation key -> EmailOutcome


def _stem(analysis: DeckAnalysis) -> str:
    return (analysis.deck_filename.rsplit(".", 1)[0] or "deck")[:60]


def email_results(analysis: DeckAnalysis, extraction: DeckExtraction, generation: str,
                  selection: list[int] | None = None, narrative: str | None = None) -> EmailOutcome:
    """Email the results (PDF + HTML + JSON) once per generation. Never raises."""
    previous = ss.emails.get(generation)
    if previous is not None and previous.sent:
        return previous
    stem = _stem(analysis)
    files: dict[str, bytes] = {}
    try:
        content = ReportContent.from_analysis(
            analysis, prepared_by=SETTINGS.prepared_by, review_date=date.today(), narrative_override=narrative
        )
        chosen = selection if selection is not None else auto_select(content)
        files[f"{stem}_headline_review.pdf"] = render_pdf(content, chosen).pdf
    except Exception as exc:  # noqa: BLE001 - the email still goes out with the other files
        log.warning("PDF not attached to results email: %s", type(exc).__name__)
    files[f"{stem}_full_review.html"] = to_html(analysis).encode("utf-8")
    files[f"{stem}_analysis.json"] = to_json(analysis, extraction).encode("utf-8")
    outcome = send_results_email(analysis, files, settings=SETTINGS, generation=generation)
    ss.emails[generation] = outcome
    return outcome


# ---------------------------------------------------------------------------
# Sidebar: configuration status and options
# ---------------------------------------------------------------------------

soffice = find_libreoffice(SETTINGS.libreoffice_path)
with st.sidebar:
    st.header("Setup")
    key_ok = has_api_key(SECRETS)
    st.markdown(("✅" if key_ok else "❌") + " Anthropic API key " + ("found" if key_ok else "not set"))
    st.markdown(("✅ Model: `" + SETTINGS.model + "`") if SETTINGS.model else "❌ ANTHROPIC_MODEL not set")
    st.markdown(("✅ LibreOffice found" if soffice else "⚠️ LibreOffice not found (needed for .ppt and PPTX thumbnails)"))
    has_ocr = ocr_available()
    st.markdown("✅ OCR (Tesseract) available" if has_ocr else "ℹ️ OCR not installed (vision model is used instead)")
    if SETTINGS.notify_available:
        st.markdown("✅ Results email to " + ", ".join(SETTINGS.notify_to))
    else:
        st.markdown("ℹ️ Results email off (set RESEND_API_KEY to enable)")
    st.caption(f"Limits: {SETTINGS.max_file_mb} MB per file, {SETTINGS.max_slides} slides per deck.")
    if not key_ok:
        st.info("Copy `.env.example` to `.env` and set `ANTHROPIC_API_KEY`, then restart the app.")

    st.header("Options")
    include_notes = st.checkbox("Include speaker notes", value=False, help="Notes are labelled as a separate evidence source.")
    include_hidden = st.checkbox("Include hidden slides", value=False, help="Hidden slides are labelled as a separate evidence source.")
    send_all_images = st.checkbox(
        "Send every slide image to the model", value=False,
        help="By default only image-based and visual slides (charts, pictures, diagrams) are sent as images.",
    )
    use_ocr = st.checkbox("OCR fallback for image-based slides", value=False, disabled=not has_ocr)

# ---------------------------------------------------------------------------
# 1. Hero card: upload
# ---------------------------------------------------------------------------

ui.brand()
with st.container(key="tc-hero"):
    ui.hero_header(
        "Deck Analyzer",
        "Pitch Deck",
        "Headline Review",
        "Upload a pitch deck and get evidence-supported slide headlines plus a polished one-page review PDF, "
        "analyzed by Claude.",
    )
    upload = st.file_uploader(
        "Upload a deck: PDF, PowerPoint (.pptx), or legacy PowerPoint (.ppt)", type=["pdf", "pptx", "ppt"]
    )
    ui.disclosure(
        [
            f"PDF · PPTX · PPT up to {SETTINGS.max_file_mb} MB and {SETTINGS.max_slides} slides.",
            "The uploaded file is processed on the server in temporary storage and is never modified.",
            "Deck content is sent to Anthropic only when you click Analyze deck.",
        ],
        emails=list(SETTINGS.notify_to) if SETTINGS.notify_available else None,
    )
ui.footer()

if upload is not None:
    upload_name, data = upload.name, upload.getvalue()
elif "_test_upload" in ss:  # automated UI tests (Streamlit AppTest cannot drive file_uploader)
    upload_name, data = ss["_test_upload"]
else:
    st.stop()

file_sig = hashlib.sha256(data).hexdigest() + (":ocr" if use_ocr else "")
if ss.get("file_sig") != file_sig:
    ss.pop("extraction", None)
    try:
        with st.status("Extracting slide content…", expanded=True) as status:
            extraction = extract_deck(data, upload_name, SETTINGS, ocr=use_ocr, progress=lambda m: status.write(m))
            status.update(label=f"Extracted {extraction.total_slides} slides.", state="complete", expanded=False)
    except HeadlineTunerError as exc:
        st.error(exc.user_message)
        st.stop()
    except Exception as exc:  # noqa: BLE001
        log.error("Unexpected extraction failure: %s", type(exc).__name__)
        st.error("The file could not be processed. It may be damaged or in an unusual format. "
                 "Try exporting it to PDF and uploading the PDF.")
        st.stop()
    ss.file_sig = file_sig
    ss.extraction = extraction
    ss.original_titles = {s.number: s.title for s in extraction.slides}
    ss.appendix_default = extraction.suggest_appendix_start()

extraction: DeckExtraction = ss.extraction

# ---------------------------------------------------------------------------
# 2–4. Extraction status, thumbnails, title corrections, appendix
# ---------------------------------------------------------------------------

ui.step(2, "Check the extracted slides")
hidden_count = sum(s.hidden for s in extraction.slides)
warned = [s for s in extraction.slides if s.warnings]
c1, c2, c3, c4 = st.columns(4)
c1.metric("Slides", extraction.total_slides)
c2.metric("Hidden", hidden_count)
c3.metric("Slides with warnings", len(warned))
c4.metric("Thumbnails", "Yes" if extraction.rendering_available else "No")
for w in extraction.warnings:
    st.warning(w)
if not extraction.rendering_available and extraction.file_format != "pdf" and not soffice:
    st.caption(INSTALL_INSTRUCTIONS)

options_labels = ["No appendix"] + [f"Slide {s.number}: {s.display_title[:60]}" for s in extraction.slides]
default_idx = ss.appendix_default if ss.get("appendix_default") else 0
appendix_choice = st.selectbox(
    "Where does the appendix start?", range(len(options_labels)), index=default_idx,
    format_func=lambda i: options_labels[i], key=f"appendix_{file_sig}",
    help="Slides from this point on are reviewed separately from the main narrative.",
)
extraction.apply_appendix_start(appendix_choice or None)

st.caption("Correct any titles below. Uncertain titles are flagged; corrected titles are used as the 'current title'.")
cols_per_row = 3
for row_start in range(0, extraction.total_slides, cols_per_row):
    cols = st.columns(cols_per_row)
    for col, slide in zip(cols, extraction.slides[row_start : row_start + cols_per_row]):
        with col.container(border=True):
            label = f"**Slide {slide.number}**"
            tags = []
            if slide.section == "appendix":
                tags.append("appendix")
            if slide.hidden:
                tags.append("hidden" + ("" if include_hidden else " — excluded"))
            if slide.title_uncertain:
                tags.append("⚠️ title uncertain")
            st.markdown(label + (" · " + " · ".join(tags) if tags else ""))
            if slide.thumbnail_png:
                st.image(slide.thumbnail_png, width="stretch")
            original = ss.original_titles.get(slide.number) or ""
            new_title = st.text_input(
                "Current title", value=slide.title or "", key=f"title_{file_sig}_{slide.number}",
                placeholder=NO_TITLE, label_visibility="collapsed",
            )
            if new_title.strip() != (slide.title or "").strip():
                slide.title = new_title.strip() or None
                slide.title_edited = new_title.strip() != original.strip()
            for w in slide.warnings:
                st.caption("⚠️ " + w)
            with st.expander("Extracted content"):
                if slide.title_source:
                    st.caption(f"Title source: {slide.title_source} (confidence: {slide.title_confidence})")
                for t in slide.text:
                    st.text(t)
                for table in slide.tables:
                    st.table(table)
                for chart in slide.chart_data:
                    st.text(chart)
                if slide.ocr_text:
                    st.text("OCR: " + slide.ocr_text)
                if slide.notes:
                    st.caption("Speaker notes" + ("" if include_notes else " (excluded)") + ":")
                    st.text(slide.notes)
                if not (slide.text or slide.tables or slide.chart_data or slide.ocr_text):
                    st.caption("No text extracted.")

# ---------------------------------------------------------------------------
# 5. Analysis
# ---------------------------------------------------------------------------

ui.step(3, "Analyze the deck")
options = AnalysisOptions(include_notes=include_notes, include_hidden=include_hidden, send_all_images=send_all_images)
included = options.included_slides(extraction)
image_count = sum(
    1 for s in included if s.thumbnail_png and (send_all_images or s.needs_vision or s.has_visual_content)
)
st.info(
    f"**Privacy notice:** when you click **Analyze deck**, the extracted text of {len(included)} slide(s)"
    + (f" and {image_count} slide image(s)" if image_count else "")
    + f" will be sent to the Anthropic API (model `{SETTINGS.model or 'not configured'}`) for analysis. "
    + ("Speaker notes are included. " if include_notes else "Speaker notes are not sent. ")
    + ("Hidden slides are included. " if include_hidden else "Hidden slides are not sent. ")
    + "Nothing is sent before you click. Deck content is treated as data, never as instructions."
    + (
        f" When the analysis finishes, the results (one-page PDF, full review, and JSON, which include extracted "
        f"deck text) are emailed automatically via Resend to {', '.join(SETTINGS.notify_to)}."
        if SETTINGS.notify_available
        else ""
    )
    + (
        " On this hosted version, the uploaded file is processed on the server in temporary storage that is "
        "deleted after extraction; results are held only in this session's memory and are not saved."
        if SETTINGS.hosted
        else ""
    )
)

analysis_key = hashlib.sha256(
    json.dumps(
        {
            "file": file_sig,
            "slides": [(s.number, s.display_title, s.section) for s in extraction.slides],
            "options": options.model_dump(),
            "model": SETTINGS.model,
            "effort": SETTINGS.effort,
        },
        sort_keys=True,
    ).encode()
).hexdigest()
cached: DeckAnalysis | None = ss.analysis_cache.get(analysis_key)

can_analyze = key_ok and bool(SETTINGS.model)
b1, b2 = st.columns([1, 3])
clicked = b1.button("Analyze deck", type="primary", disabled=not can_analyze)
force = b2.checkbox("Re-run even if this exact deck and settings were already analyzed", value=False)
if not can_analyze:
    st.caption("Analysis needs ANTHROPIC_API_KEY and ANTHROPIC_MODEL (see the sidebar).")

if clicked:
    if cached is not None and not force:
        st.success("This deck was already analyzed with these settings in this session; showing the cached result.")
    else:
        try:
            llm = AnthropicLLM(get_api_key(SECRETS), SETTINGS)
            with st.status("Analyzing…", expanded=True) as status:
                bar = st.progress(0.0)

                def progress(msg: str, frac: float) -> None:
                    status.update(label=msg)
                    bar.progress(min(max(frac, 0.0), 1.0))

                result = analyze_deck(extraction, options, llm, SETTINGS, progress=progress)
                status.update(label="Analysis complete.", state="complete", expanded=False)
            ss.analysis_cache[analysis_key] = result
            ss.last_analysis_key = analysis_key
            cached = result
            for k in [k for k in ss.keys() if str(k).startswith("pdfsel_") or str(k).startswith("narr_")]:
                del ss[k]
            if SETTINGS.notify_available:
                with st.spinner(f"Emailing results to {', '.join(SETTINGS.notify_to)}…"):
                    email_results(result, extraction, f"{analysis_key}:{result.generated_at.isoformat()}")
        except HeadlineTunerError as exc:
            st.error(exc.user_message)
        except Exception as exc:  # noqa: BLE001
            log.error("Unexpected analysis failure: %s", type(exc).__name__)
            st.error(f"Analysis failed unexpectedly ({type(exc).__name__}). Please try again.")

analysis: DeckAnalysis | None = cached
if analysis is None and ss.get("last_analysis_key") in ss.analysis_cache:
    analysis = ss.analysis_cache[ss.last_analysis_key]
    st.warning("Showing the previous analysis. Titles, appendix, or options have changed since; click Analyze deck to update.")
if analysis is None:
    st.stop()

# ---------------------------------------------------------------------------
# 6–8. Results
# ---------------------------------------------------------------------------

ui.step(4, "Review the results")
shown_key = analysis_key if cached is analysis else ss.get("last_analysis_key", analysis_key)
outcomes = [o for g, o in ss.emails.items() if g.startswith(shown_key)]
if outcomes:
    latest = outcomes[-1]
    if latest.sent:
        st.success("✉️ " + latest.status_text + " (" + ", ".join(latest.attachments) + ").")
    else:
        st.warning("✉️ " + latest.status_text + ". Download the files below instead.")
attention = [r for r in analysis.slide_reviews if r.needs_attention]
m1, m2, m3 = st.columns(3)
m1.metric("Slides reviewed", len(analysis.slide_reviews))
m2.metric("Needing attention", len(attention))
m3.metric("Priority improvements", len(analysis.priority_improvements))
if analysis.extraction_limitations or analysis.analysis_warnings:
    with st.expander(f"⚠️ Extraction limitations and analysis warnings ({len(analysis.extraction_limitations) + len(analysis.analysis_warnings)})", expanded=True):
        for w in analysis.analysis_warnings + analysis.extraction_limitations:
            st.warning(w)

thumbs = {s.number: s.thumbnail_png for s in extraction.slides}


def show_review(r: SlideReview) -> None:
    with st.container(border=True):
        left, right = st.columns([1, 3])
        with left:
            st.markdown(f"**Slide {r.slide_number}**")
            if thumbs.get(r.slide_number):
                st.image(thumbs[r.slide_number], width="stretch")
        with right:
            st.caption(f"Current title: {r.current_title}")
            st.markdown(f"#### {r.recommended_headline}")
            status = EVIDENCE_STATUS_LABELS[r.evidence_status]
            claims = ", ".join(CLAIM_TYPE_LABELS.get(c, c) for c in r.claim_types) or "—"
            st.markdown(f"`{status}` · priority **{r.improvement_priority}** · claims: {claims}")
            sig_label = "Customer benefit" if r.significance_type == "customer_benefit" else "Investor significance"
            st.markdown(f"**Feature / key fact:** {r.key_fact}  \n**{sig_label}:** {r.significance}")
            refs = ", ".join(map(str, r.supporting_slides)) + (" (cross-slide)" if r.cross_slide_support else "")
            st.markdown(f"**Evidence** (slides {refs}; {r.evidence_source.replace('_', ' ')}): “{r.evidence_excerpt}”")
            if r.evidence_gap:
                (st.warning if r.evidence_status != "supported" else st.caption)(f"Evidence gap: {r.evidence_gap}")
            if r.conflict_note:
                st.error(f"Conflicting figures: {r.conflict_note}")
            if r.narrative_revision_reason:
                st.caption(f"Revised in narrative pass: {r.narrative_revision_reason}")
            for w in r.validation_warnings:
                st.warning(w)


tab_full, tab_story, tab_prio, tab_app, tab_pdf, tab_dl = st.tabs(
    ["Full review", "Headlines narrative", "Priority improvements", "Appendix", "One-page PDF", "Downloads"]
)
with tab_full:
    if analysis.narrative_assessment:
        st.markdown(f"**Narrative assessment:** {analysis.narrative_assessment}")
    for r in analysis.main_reviews:
        show_review(r)
with tab_story:
    st.caption("Main-deck headlines only, in slide order.")
    st.markdown("\n".join(f"{l.slide_number}. {l.headline}" for l in analysis.main_narrative))
    if analysis.narrative_gaps:
        st.markdown("**Narrative gaps**")
        st.markdown("\n".join(f"- {g}" for g in analysis.narrative_gaps))
with tab_prio:
    if not analysis.priority_improvements:
        st.write("No priority improvements were identified.")
    for p in analysis.priority_improvements:
        refs = f" _(slides {', '.join(map(str, p.slide_refs))})_" if p.slide_refs else ""
        st.markdown(f"**{p.rank}. {p.title}** — {p.detail}{refs}")
with tab_app:
    if not analysis.appendix_reviews:
        st.write("No appendix slides were marked.")
    else:
        if analysis.appendix_summary:
            st.write(analysis.appendix_summary)
        for r in analysis.appendix_reviews:
            show_review(r)

pdf_bytes: bytes | None = None
with tab_pdf:
    narr_key = f"narr_{analysis_key}"
    ss.setdefault(narr_key, analysis.narrative_assessment)
    narrative = st.text_area(
        "Narrative assessment shown in the PDF (edit to shorten if needed)", key=narr_key, height=100,
    )
    base_content = ReportContent.from_analysis(
        analysis, prepared_by=SETTINGS.prepared_by, review_date=date.today(), narrative_override=narrative
    )
    main_numbers = [r.slide_number for r in base_content.rows]
    sel_key = f"pdfsel_{analysis_key}"
    if sel_key not in ss:
        ss[sel_key] = auto_select(base_content)
    titles = {r.slide_number: r.current_title for r in base_content.rows}
    if st.button("Reset to automatic selection"):
        ss[sel_key] = auto_select(base_content)
    selection = st.multiselect(
        "Main-deck slides shown in the PDF table", main_numbers, key=sel_key,
        format_func=lambda n: f"{n} — {titles.get(n, '')[:50]}",
    )
    if len(selection) < len(main_numbers):
        st.info(f"Showing {len(selection)} of {len(main_numbers)} main-deck slides; see the full review for all recommendations.")
    try:
        result = render_pdf(base_content, selection)
        pdf_bytes = result.pdf
        if result.replaced_chars:
            st.warning("Some characters are not available in the PDF font and were substituted: "
                       + " ".join(sorted(result.replaced_chars)))
        st.caption(f"One page, US Letter · table text {result.table_font_size:g} pt")
        if SETTINGS.notify_available:
            edit_sig = hashlib.sha256(json.dumps([sorted(selection), narrative]).encode()).hexdigest()[:16]
            edited_key = f"{shown_key}:edit:{edit_sig}"
            already = ss.emails.get(edited_key)
            if already is not None and already.sent:
                st.caption("✉️ This version of the PDF has been emailed.")
            elif st.button(f"Email this version to {', '.join(SETTINGS.notify_to)}"):
                with st.spinner("Sending…"):
                    sent = email_results(analysis, extraction, edited_key, selection=selection, narrative=narrative)
                (st.success if sent.sent else st.warning)("✉️ " + sent.status_text + ".")
        st.image(preview_png(pdf_bytes, dpi=110), width=700)
    except LayoutOverflowError as exc:
        st.error(exc.user_message)
    except Exception as exc:  # noqa: BLE001
        log.error("PDF rendering failed: %s", type(exc).__name__)
        st.error("The PDF could not be rendered. The full analysis is still available as JSON and HTML in Downloads.")

with tab_dl:
    stem = _stem(analysis)
    if pdf_bytes:
        st.download_button("Download one-page PDF", pdf_bytes, file_name=f"{stem}_headline_review.pdf", mime="application/pdf", type="primary")
    else:
        st.caption("Fix the PDF selection in the One-page PDF tab to enable the PDF download.")
    st.download_button("Download full review (HTML)", to_html(analysis), file_name=f"{stem}_full_review.html", mime="text/html")
    st.download_button("Download full analysis (JSON)", to_json(analysis, extraction), file_name=f"{stem}_analysis.json", mime="application/json")
