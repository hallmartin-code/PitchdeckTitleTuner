# Pitch Deck Headline Review: Document Template

This is the general structure for every review the app generates. It contains no company data.
`[Square brackets]` mark content filled from each deck's analysis, and `{braces}` mark fields inserted
by the app.

- **Machine-readable template, used by the app:** `headline_review_template.json`. Override it with
  `REPORT_TEMPLATE_PATH`.
- **Visual preview:** `headline_review_template_preview.pdf`.

---

## Part A: One-page PDF review

**Page:** US Letter, portrait, exactly one page. Margins are 0.7 in left and right, 0.62 in top, and
0.5 in bottom. A navy bar with a thin teal accent runs across the top edge.
**Typography:** embedded DejaVu Sans Condensed. Table text is 9.5, 9.25, or 9 pt and never smaller.
Body text is 9.5 pt and the footer is 7.5 pt.
**Palette:** primary navy `#1B2A4A`, accent teal `#0E7C72`, row tint `#ECF6F4`, muted text `#5A6472`,
and rules `#CAD4DD`.

### 1. Header

```
PITCH DECK HEADLINE REVIEW                                   ← eyebrow (teal, 9 pt, bold)
[Company name]                                               ← title (navy, 19 pt, bold)
Source: {source_filename} · {slide_count_summary} · Reviewed {review_date}     ← meta (8.5 pt, grey)
─────────────────────────────────────────────────────────    ← teal rule
```

- If the deck does not identify the company, the title reads **Pitch Deck Headline Review** and the
  eyebrow is omitted.
- `{slide_count_summary}` follows the pattern `N slides (X main deck, Y appendix, Z hidden, not reviewed)`.
  Each part in brackets appears only when it applies.

### 2. Narrative assessment

**Heading:** Narrative assessment
**Content:** `[At most 55 words: how well the current titles tell the story and what the recommended
headlines change.]`
The section is omitted if the narrative assessment is empty.

### 3. Recommended headlines

**Heading:** Recommended headlines
**Notes line** (grey, shown only when it applies):
- `Showing {shown} of {total} main-deck slides; see the full review for all recommendations.`
- `Appendix slides ({appendix_count}) are reviewed in the full analysis.`

| Slide | Current title | Recommended headline |
|---|---|---|
| `{slide_number}` | `[Current or user-corrected title, or "No identifiable title."]` | `[One complete, active-voice sentence, 10–18 words, stating the takeaway]` `†`* |
| … | … | … |

- **Rows:** main-deck slides only, in slide order. If not all rows fit, the opening slide is kept first,
  then slides are added by improvement priority (high, medium, low). The user can change the selection.
- **Column widths:** Slide 0.52 in, Current title 1.95 in, and Recommended headline takes the remaining
  width.
- **Styling:** navy header row with white text, alternating white and tinted rows, and the headline in bold
  navy.
- \* **Flag `†`:** shown when the evidence status is anything other than *supported*. A legend appears
  below the table: `† Evidence in the deck is limited or conflicting; the headline is deliberately
  conservative. See the full review for the evidence gap.`

### 4. Priority improvements

**Heading:** Priority improvements
**Items:** up to five, ranked most important first, and fewer when fewer are justified.

```
1. [Improvement title, ≤ 8 words] — [What to change and why, ≤ 30 words.] (Slide N | Slides N, M)
2. …
```

### 5. Footer

```
Findings are based solely on the content of the supplied deck ({source_filename}) and have not been
independently verified; company claims are reported as stated. Recommended headlines are suggestions
only; slide layouts were not edited or measured. [Prepared by {prepared_by}.] Generated {review_date}
with Pitch Deck Headline Tuner.
```

### Layout rules

- Content is measured before rendering. It is never clipped, never shrunk below 9 pt, and never spills
  onto a second page. If the selected rows do not fit, the user is asked to reduce the selection.
- All content is escaped before insertion. Characters the font cannot draw are substituted, and the
  substitution is reported to the user.

---

## Part B: Fields to analyze for every deck

The fields below make up the full review. It is shown in the app and exported as HTML and JSON. The PDF
is a condensed view of these fields.

### Deck level

| Field | Label | Content |
|---|---|---|
| `company_name` | Company name | Only if the deck identifies it; otherwise empty. |
| `deck_filename` | Source filename | Name of the uploaded file. |
| `total_slides` | Total slides | All slides in the file, including hidden ones. |
| `included_slides` | Reviewed slides | One-based slide numbers that were analyzed. |
| `extraction_limitations` | Extraction limitations | Unreadable or partially extracted content, with affected slides. |
| `narrative_assessment` | Narrative assessment | Short deck-wide judgement of the story told by the headlines. |
| `slide_reviews` | Slide-by-slide reviews | Exactly one review per included slide, in slide order. |
| *(derived)* | Main-deck narrative | The recommended headlines alone, in slide order. |
| `narrative_gaps` | Narrative gaps | Story elements an investor would expect that the deck does not evidence. |
| `appendix_summary` | Appendix review | One or two sentences on the appendix slides, reviewed separately. |
| `priority_improvements` | Priority improvements | Up to five ranked improvements. |

### Per slide (one review for every included slide)

| Field | Label | Content / allowed values |
|---|---|---|
| `slide_number` | Slide | One-based number in original order. |
| `current_title` | Current title | Extracted or user-corrected title, or "No identifiable title." |
| `recommended_headline` | Recommended headline | One complete sentence with an active verb, ideally 10–18 words. |
| `key_fact` | Feature / key fact | The capability or fact the headline is built on. |
| `significance` | Customer benefit / investor significance | Why the fact matters: to a customer on product, solution, or technology slides, and to an investor on other slides. |
| `significance_type` | Significance type | `customer_benefit` · `investor_significance` |
| `supporting_slides` | Supporting slides | Slide numbers whose content supports the headline. |
| `cross_slide_support` | Cross-slide support | True when support comes from other slides. |
| `evidence_excerpt` | Evidence | Short verbatim quote, or a factual description of visible image content. |
| `evidence_source` | Evidence source | `slide_text` · `visual` · `speaker_notes` · `hidden_slide` · `mixed` |
| `evidence_status` | Evidence status | `supported` · `intended_benefit` · `insufficient_evidence` · `conflicting_evidence` |
| `claim_types` | Claim types | `actual_result` · `forecast` · `target` · `pilot` · `signed_contract` · `expression_of_interest` · `company_claim` · `none` |
| `evidence_gap` | Evidence gap | Evidence that is missing or would strengthen the headline. |
| `conflict_note` | Conflicting figures | Conflicting figures and the slides they appear on. |
| `improvement_priority` | Priority | `high` · `medium` · `low` |
| `section` | Section | `main` · `appendix` |

### Per priority improvement

| Field | Label | Content |
|---|---|---|
| `rank` | Rank | 1–5, most important first. |
| `title` | Title | At most 8 words. |
| `detail` | Detail | At most 30 words. |
| `slide_refs` | Slides | Slide numbers the improvement applies to. |

---

## Editing the template

Edit `headline_review_template.json` to change headings, wording, colors, sizes, column widths, footer
text, or the order of sections. You can also turn sections off with `"enabled": false`. The app validates
the template when it renders the PDF and rejects these changes:

- table text below 9 pt
- more than one page or more than five priority items
- unknown or duplicate section IDs
- a missing headline table
- a coverage note without `{shown}` and `{total}`
- colors that are not `#RRGGBB`
