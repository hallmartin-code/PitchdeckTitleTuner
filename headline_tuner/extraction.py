"""Top-level deck extraction: validates the upload, converts when needed, and
returns slides in original order with one-based numbers.

The uploaded bytes are copied into a temporary directory; the user's source
file is never modified. Temporary files are removed when extraction ends.
"""

from __future__ import annotations

import hashlib
import logging
import os
import tempfile
from collections.abc import Callable
from pathlib import Path

import pymupdf

from .config import Settings
from .convert import INSTALL_INSTRUCTIONS, convert_with_libreoffice, find_libreoffice
from .errors import ConversionFailedError, ConversionUnavailableError, FileTooLargeError
from .extract_pdf import OcrFn, extract_pdf, render_pdf_thumbnails
from .extract_pptx import extract_pptx
from .filetypes import detect_format, extension_matches
from .models import DeckExtraction, SlideContent
from .ocr import INSTALL_HINT as OCR_INSTALL_HINT
from .ocr import OCR_DPI, find_tesseract, ocr_png
from .textutils import numeric_labels

log = logging.getLogger(__name__)

Progress = Callable[[str], None]


def ocr_available() -> bool:
    return find_tesseract() is not None


def _ocr_function(settings: Settings) -> OcrFn:
    tesseract = find_tesseract()
    language = os.environ.get("OCR_LANGUAGE", "eng")

    def run(png: bytes) -> str:
        return ocr_png(png, tesseract=tesseract, language=language, timeout_s=settings.conversion_timeout_s)

    return run


def _noop(_: str) -> None:
    pass


def extract_deck(
    data: bytes,
    filename: str,
    settings: Settings,
    *,
    ocr: bool = False,
    render: bool = True,
    progress: Progress | None = None,
) -> DeckExtraction:
    progress = progress or _noop
    if len(data) > settings.max_file_bytes:
        raise FileTooLargeError(
            f"The file is {len(data) / 1024 / 1024:.1f} MB; the limit is {settings.max_file_mb} MB."
        )
    fmt = detect_format(data)
    warnings: list[str] = []
    if not extension_matches(filename, fmt):
        warnings.append(f"The file extension does not match its contents; it was processed as {fmt.upper()}.")
    sha = hashlib.sha256(data).hexdigest()
    soffice = find_libreoffice(settings.libreoffice_path)
    ocr_fn: OcrFn | None = None
    if ocr:
        if ocr_available():
            ocr_fn = _ocr_function(settings)
        else:
            warnings.append(
                "OCR was requested but Tesseract is not installed; the vision model is used instead. "
                + OCR_INSTALL_HINT
            )

    with tempfile.TemporaryDirectory(prefix="headline_tuner_") as tmp_name:
        tmp = Path(tmp_name)
        src = tmp / f"deck.{fmt}"  # never reuse the user's filename in paths
        src.write_bytes(data)
        log.info("Extracting %s deck (%d bytes)", fmt, len(data))

        if fmt == "pdf":
            progress("Reading PDF pages…")
            slides, rendered = extract_pdf(src, max_slides=settings.max_slides, render=render, ocr=ocr_fn)
        else:
            if fmt == "ppt":
                if not soffice:
                    raise ConversionUnavailableError(
                        "Legacy .ppt files must be converted before they can be read, and LibreOffice "
                        f"was not found. {INSTALL_INSTRUCTIONS} Alternatively, open the deck in "
                        "PowerPoint, save it as .pptx (or export to PDF), and upload that file."
                    )
                progress("Converting legacy PPT to PPTX with LibreOffice…")
                pptx_path = convert_with_libreoffice(
                    src, tmp / "converted", "pptx", soffice=soffice, timeout_s=settings.conversion_timeout_s
                )
                warnings.append("Converted from legacy .ppt with LibreOffice; check titles and layout details.")
            else:
                pptx_path = src
            progress("Reading slides…")
            slides = extract_pptx(pptx_path, max_slides=settings.max_slides)
            rendered = False
            if render or ocr_fn:
                rendered = _render_pptx_thumbnails(
                    pptx_path, slides, soffice, settings, tmp, warnings, progress, ocr_fn
                )
                if not render:
                    for s in slides:
                        s.thumbnail_png = None
                    rendered = False

    if not rendered and render:
        if fmt == "pdf":
            warnings.append("Slide images could not be rendered.")
        visual = [s.number for s in slides if s.needs_vision]
        if visual:
            warnings.append(
                "Image-based slides could not be reviewed visually because slide images are unavailable: "
                + ", ".join(str(n) for n in visual)
                + "."
            )
            for s in slides:
                if s.needs_vision:
                    s.warnings.append("Slide image unavailable; visual content on this slide was not reviewed.")

    extraction = DeckExtraction(
        filename=filename,
        file_format=fmt,
        source_sha256=sha,
        slides=slides,
        warnings=warnings,
        rendering_available=rendered,
    )
    suggested = extraction.suggest_appendix_start()
    extraction.apply_appendix_start(suggested)
    return extraction


def _render_pptx_thumbnails(
    pptx_path: Path,
    slides: list[SlideContent],
    soffice: str | None,
    settings: Settings,
    tmp: Path,
    warnings: list[str],
    progress: Progress,
    ocr_fn: OcrFn | None = None,
) -> bool:
    if not soffice:
        warnings.append(
            "Slide thumbnails are unavailable because LibreOffice is not installed; text was still "
            f"extracted. {INSTALL_INSTRUCTIONS}"
        )
        return False
    progress("Rendering slide images with LibreOffice…")
    try:
        pdf_path = convert_with_libreoffice(
            pptx_path, tmp / "rendered", "pdf", soffice=soffice, timeout_s=settings.conversion_timeout_s
        )
    except (ConversionFailedError, ConversionUnavailableError) as exc:
        warnings.append(f"Slide thumbnails are unavailable: {exc.user_message}")
        return False
    doc = pymupdf.open(stream=pdf_path.read_bytes(), filetype="pdf")
    try:
        visible = [s for s in slides if not s.hidden]
        if doc.page_count == len(slides):
            targets = slides
        elif doc.page_count == len(visible):
            targets = visible
            if len(visible) != len(slides):
                warnings.append("Hidden slides have no thumbnail (LibreOffice does not render hidden slides).")
        else:
            warnings.append(
                "Rendered page count did not match the slide count, so thumbnails were not attached "
                "to avoid mislabelling slides."
            )
            return False
        thumbs = render_pdf_thumbnails(doc)
        for s, png in zip(targets, thumbs):
            s.thumbnail_png = png
        if ocr_fn is not None:
            for i, s in enumerate(targets):
                if s.needs_vision:
                    _ocr_slide(s, doc[i], ocr_fn)
    finally:
        doc.close()
    return any(t is not None for t in thumbs)


def _ocr_slide(slide: SlideContent, page: pymupdf.Page, ocr_fn: OcrFn) -> None:
    try:
        slide.ocr_text = ocr_fn(page.get_pixmap(dpi=OCR_DPI, alpha=False).tobytes("png"))
    except Exception:
        slide.warnings.append("OCR failed on this slide.")
        return
    if slide.ocr_text:
        slide.warnings.append("Text was recovered with OCR; OCR text may contain recognition errors.")
        slide.numeric_labels = numeric_labels(slide.numeric_labels + [slide.ocr_text])
