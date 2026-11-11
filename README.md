# Pitch Deck Headline Tuner

A local Streamlit app that reads a startup investor pitch deck (PDF, PPTX, or legacy PPT) and recommends
evidence-supported slide headlines. Each headline states the slide's main point and why it matters to a
customer or an investor. The main output is a **one-page US Letter PDF review**. The full slide-by-slide
analysis, a headlines-only narrative, and up to five priority improvements are also shown in the app and
can be downloaded as JSON and HTML.

The app never modifies the uploaded file, and it does not edit slide layouts.

## How it works

| Stage | Module | What happens |
|---|---|---|
| Validate | `headline_tuner/filetypes.py` | Checks the real file format from its bytes, not its extension. Rejects empty, corrupt, encrypted, unsupported, and oversized files. |
| Extract | `extract_pdf.py`, `extract_pptx.py`, `extraction.py`, `convert.py` | Extracts slide text, tables, embedded chart data, notes, hidden flags, and thumbnails, keeping the original slide order. Titles come from title placeholders, font size, and position, and uncertain titles are flagged. Legacy `.ppt` files are converted with LibreOffice. |
| Analyze | `prompts.py`, `llm.py`, `analyzer.py` | Reviews slides in batches using structured JSON output, then runs a deck-wide narrative consistency pass. |
| Validate output | `validation.py` | Checks coverage (one review per included slide), slide references, evidence excerpts, and figures that do not appear in the deck. Retries are bounded. |
| Render | `pdf_report.py`, `fonts.py` | Lays out the PDF and measures it before drawing. Fonts are embedded, and the table is never below 9 pt, never clipped, and never spills onto a second page. |
| Export | `exports.py` | Writes the full review as JSON and as a self-contained HTML page. |

The headline rubric (feature-to-benefit, active verb, 10–18 words, conservative when evidence is thin, and so
on) is built into `headline_tuner/prompts.py`, so analysis never fetches anything at runtime. The rubric is
adapted from ViewSonic's
[How to Construct Perfect Slide Headlines for Your Pitch Deck, Part 2](https://www.viewsonic.com/library/business/how-to-construct-perfect-slide-headlines-for-your-pitch-deck-part-2/).

## Requirements

- **Python 3.11 or newer**
- **An Anthropic API key** (needed only for analysis; extraction works without one)
- **LibreOffice** (optional). It is needed for legacy `.ppt` files and for PowerPoint slide thumbnails.
  Without it, PDF decks work fully and PPTX text extraction still works.
  - Windows: `winget install TheDocumentFoundation.LibreOffice`
  - macOS: `brew install --cask libreoffice`
  - Debian/Ubuntu: `sudo apt install libreoffice-impress`
- **Tesseract** (optional), for the OCR fallback on image-based slides. The app finds it on PATH or in the
  standard install folder; otherwise set `TESSERACT_PATH`. Without it, image-based slides are still sent to
  the vision-capable model as images.
  - Windows: `winget install UB-Mannheim.TesseractOCR`
  - macOS: `brew install tesseract`
  - Debian/Ubuntu: `sudo apt install tesseract-ocr`

## Install and launch

### Windows (VS Code terminal, PowerShell)

```powershell
cd "C:\path\to\PitchdeckTitleTuner"
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements-dev.txt
Copy-Item .env.example .env
code .env        # set ANTHROPIC_API_KEY (and review ANTHROPIC_MODEL)
.\run.ps1        # always starts the app with this project's .venv
```

If PowerShell blocks `Activate.ps1` or `run.ps1`, run
`Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned` once, or skip activation and use
`.\.venv\Scripts\python.exe -m streamlit run app.py`.

### macOS / Linux (VS Code terminal, bash or zsh)

```bash
cd /path/to/PitchdeckTitleTuner
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements-dev.txt
cp .env.example .env
code .env        # set ANTHROPIC_API_KEY (and review ANTHROPIC_MODEL)
./run.sh         # always starts the app with this project's .venv
```

The app opens at <http://localhost:8501>. Start it with `run.ps1` / `run.sh` (or the venv's python) rather than a bare
`streamlit run`: on machines with several Python installations, a bare command can pick up an older global
Streamlit or Anthropic SDK. The app detects this and shows how to restart correctly. To try it without a real deck, upload
`samples/tidewater_seed_deck.pptx` or `samples/tidewater_seed_deck.pdf`. Both are synthetic.

### Check your API key

```bash
python scripts/check_api.py          # one tiny call to confirm key, model and settings
python scripts/check_api.py --full   # full analysis of the synthetic deck + one-page PDF (real tokens)
```

### Deploy as a web app

See [DEPLOY.md](DEPLOY.md) for Railway: a Dockerfile with LibreOffice and Tesseract, the variables,
and the CLI commands.

### Automated results email

When `RESEND_API_KEY` is set, every new analysis is emailed automatically to `NOTIFY_TO` (default
`Info@tencapital.group`) from `NOTIFY_FROM`. The email carries a summary that follows the report template,
with the one-page PDF, the HTML full review, and the JSON analysis attached. A cached re-click does not
send it again. After editing the PDF's slide selection or narrative, **Email this version** sends the
updated PDF. Recipients come only from configuration, never from the UI or the deck. A failed email never
fails the analysis; the app shows the outcome, and the files remain downloadable.

## Configuration

Settings are read from environment variables, then from `.env`, then from `.streamlit/secrets.toml`.
See `.env.example` for every option.

| Variable | Default | Purpose |
|---|---|---|
| `ANTHROPIC_API_KEY` | none (required) | API key. It is never logged or embedded in code. |
| `ANTHROPIC_MODEL` | none (required) | Model ID. It is not hard-coded in the app. Use a vision-capable model. |
| `ANTHROPIC_EFFORT` | omitted | `low` to `max`, or `default`. |
| `ANTHROPIC_THINKING` | `adaptive` | Set to `off` for models without adaptive thinking. |
| `ANTHROPIC_FALLBACKS` | `default` | Server-side refusal fallbacks. Set to `off` if your model or platform rejects them. |
| `MAX_FILE_MB` / `MAX_SLIDES` | `50` / `80` | Upload limits, shown in the sidebar. Keep `.streamlit/config.toml` `maxUploadSize` in sync. |
| `ANALYSIS_BATCH_SIZE` | `8` | Slides per model request. |
| `VALIDATION_RETRIES` | `2` | Retries for malformed or incomplete model output. |
| `LIBREOFFICE_PATH` | auto-detect | Path to `soffice` if it is not found automatically. |
| `TESSERACT_PATH` / `OCR_LANGUAGE` | auto-detect / `eng` | Tesseract executable and OCR language (for example `eng+deu`). |
| `REVIEW_PREPARED_BY` | none | Optional "Prepared by" text in the PDF footer. |
| `REPORT_TEMPLATE_PATH` | bundled template | Custom one-page report template (see below). |
| `RESEND_API_KEY` | none | Turns on the automated results email. |
| `NOTIFY_TO` / `NOTIFY_FROM` | `Info@tencapital.group` / `TEN Capital Headline Tuner <reports@tencapital.group>` | Recipients (comma-separated) and sender. The sender's domain must be verified in Resend. |

## Report template

The structure of every review is defined in `templates/`:

- `headline_review_template.json` is the template the PDF renderer reads. It sets page setup, palette,
  typography, section order and headings, table columns, notes, flag legend, footer wording, and the
  analysis fields each review must contain.
- `headline_review_template.md` is a human-readable description of the same structure and fields.
- `headline_review_template_preview.pdf` shows the layout filled with placeholders only.

Edit the JSON, or point `REPORT_TEMPLATE_PATH` at a copy, to change wording, colors, or section order. The
renderer validates the template and will not accept settings that break the one-page or 9 pt guarantees.

## Using the app

1. **Upload** a PDF, PPTX, or PPT file (up to 50 MB and 80 slides by default).
2. **Check extraction.** The app shows each slide's thumbnail, warnings, and extracted content.
3. **Correct titles** that are wrong or flagged as uncertain, and **mark where the appendix starts**
   (detected automatically when a slide title starts with "Appendix").
4. Choose options in the sidebar: include speaker notes, include hidden slides, send every slide image, or
   use OCR. OCR is on by default when Tesseract is installed. Notes and hidden slides are excluded by default. When included, they are labelled as separate
   evidence sources.
5. Read the **privacy notice** and click **Analyze deck**. Nothing is sent to Anthropic before you click.
   The extracted text of the included slides, plus images of visual or image-based slides, is sent to the
   configured model.
6. **Review** the results. Extraction limitations, evidence gaps, conflicting figures, and unverified
   figures are highlighted.
7. In **One-page PDF**, adjust which main-deck slides appear in the table. For long decks the
   highest-priority slides are pre-selected, and the PDF says
   "Showing X of Y main-deck slides; see the full review for all recommendations." If your selection does not
   fit at 9 pt, the app asks you to reduce it rather than shrinking or clipping the text.
8. **Download** everything in one ZIP (one-page PDF, HTML full review, and JSON analysis including the extracted
   deck text), or each file separately. HTML and JSON are available even if PDF
   rendering fails.

Successful analyses are cached for the session. Re-clicking **Analyze deck** with the same deck, titles,
and options reuses the result instead of calling the API again.

## Evidence and safety rules

- Recommendations use only evidence from the uploaded deck. Actual results, forecasts, targets, pilots,
  signed contracts, and expressions of interest are kept distinct.
- Every review includes supporting slide numbers, an evidence excerpt or a visual description, an evidence
  status (supported, intended benefit, insufficient evidence, or conflicting evidence), and any evidence gap.
  Cross-slide support is flagged.
- Conflicting figures are flagged, not reconciled.
- Numbers in a recommended headline are checked against the extracted deck text. A headline with an
  invented figure is retried. If the figure persists, it is marked `UNVERIFIED FIGURE`. A narrative revision
  that adds a figure is rejected.
- Deck text is treated as untrusted data. It is wrapped in delimiters (any delimiter-like text inside it is
  neutralised), and the system prompt tells the model never to follow instructions found in the deck.
- Chart values are never inferred from appearance. Only printed values and embedded PPTX chart data are used.

## Privacy and reliability

- Runs locally (`localhost`). Usage statistics are disabled in `.streamlit/config.toml`.
- Uploads are processed in temporary directories that are deleted afterwards.
- Logs record operational events only, never deck content or credentials.
- LibreOffice runs through subprocess argument lists with a timeout and an isolated temporary profile.

## Tests

```bash
python -m pytest              # full suite (LibreOffice integration tests skip if it is not installed)
python -m pytest -m "not slow"  # skip LibreOffice integration tests
```

Every test uses synthetic fixtures (`headline_tuner/synthetic.py`) and a mocked model, so no test calls
the API. The tests cover:

- PDF and PPTX extraction, grouped shapes, tables, chart data, an image-based slide, an evidence-poor slide,
  a forecast, an appendix, hidden slides, notes, and a 45-slide deck
- Slide order and complete coverage, including batching, re-requesting missing slides, and duplicates
- Preservation of numerical qualifiers
- Structured-response validation and bounded retries
- Missing API key, missing model, SDK error mapping, and unavailable LibreOffice
- Exactly one PDF page, embedded fonts, the 9 pt minimum, overflow refusal, Unicode, and markup escaping
- The Streamlit flow end to end with Streamlit's `AppTest`

Regenerate the samples (synthetic decks, a one-page PDF, and HTML/JSON exports) with:

```bash
python scripts/make_samples.py
```

## Limitations

- The live Anthropic integration is exercised only by mocked tests in this repository. Run a real deck once
  after configuring your key.
- Title detection in PDFs relies on font size and position. Decks with unusual layouts may need manual
  title corrections, and the app flags uncertain titles for that reason.
- SmartArt text, text inside images (without OCR or the vision model), and chart values in PDFs that are
  not printed as text are not extracted. Affected slides are flagged.
- LibreOffice does not render hidden slides, so they have no thumbnail.
- The bundled DejaVu fonts cover Latin, Greek, Cyrillic, and common symbols. CJK and some other scripts are
  substituted in the PDF (the app warns when this happens). To use other fonts, set `PDF_FONT_DIR` to a
  folder containing replacement TTF files with the same file names as those in `assets/fonts/`.
- The figure check compares digits only. It cannot catch an invented claim written in words (for example,
  "doubled").

## Project layout

```
app.py                      Streamlit UI
headline_tuner/             extraction, analysis, validation, PDF rendering, exports
assets/fonts/               embedded DejaVu fonts (see DejaVu-LICENSE.txt)
scripts/make_samples.py     builds synthetic samples
samples/                    synthetic decks and a sample one-page review
tests/                      pytest suite
```
