import subprocess

import pytest

from headline_tuner import extraction, ocr
from headline_tuner.convert import find_libreoffice
from headline_tuner.extraction import extract_deck
from headline_tuner.models import AnalysisOptions
from headline_tuner.ocr import find_tesseract, ocr_png
from headline_tuner.prompts import deck_text

HAS_OCR = find_tesseract() is not None
ROWS = ["Leak alerts dashboard", "Zone 4 - hydrant 112 - high", "Zone 2 - main 7B - medium", "Zone 9 - valve 31 - low"]


def test_ocr_unavailable_is_reported_not_fatal(monkeypatch, deck_dir, settings):
    monkeypatch.setattr(extraction, "ocr_available", lambda: False)
    ex = extract_deck((deck_dir / "deck.pdf").read_bytes(), "deck.pdf", settings, ocr=True)
    assert any("Tesseract is not installed" in w and "winget install" in w for w in ex.warnings)
    assert ex.slide(6).ocr_text == ""


def test_ocr_uses_argument_list_and_timeout(monkeypatch):
    seen = {}

    def fake_run(cmd, **kwargs):
        seen["cmd"], seen["kwargs"] = cmd, kwargs
        return subprocess.CompletedProcess(cmd, 0, b"Line one\n\n  Line two \n", b"")

    monkeypatch.setattr(ocr.subprocess, "run", fake_run)
    assert ocr_png(b"png", tesseract="tesseract", timeout_s=9) == "Line one\nLine two"
    assert isinstance(seen["cmd"], list) and seen["cmd"][0] == "tesseract" and "stdout" in seen["cmd"]
    assert seen["kwargs"]["timeout"] == 9 and "shell" not in seen["kwargs"]


def test_ocr_failure_is_flagged_per_slide(monkeypatch, deck_dir, settings):
    monkeypatch.setattr(extraction, "ocr_available", lambda: True)
    monkeypatch.setattr(extraction, "_ocr_function", lambda s: (lambda png: (_ for _ in ()).throw(RuntimeError("x"))))
    ex = extract_deck((deck_dir / "deck.pdf").read_bytes(), "deck.pdf", settings, ocr=True)
    assert "OCR failed on this slide." in ex.slide(6).warnings


@pytest.mark.skipif(not HAS_OCR, reason="Tesseract not installed")
def test_ocr_recovers_text_from_image_slide_pdf(deck_dir, settings):
    ex = extract_deck((deck_dir / "deck.pdf").read_bytes(), "deck.pdf", settings, ocr=True)
    s = ex.slide(6)
    for row in ROWS:
        assert row in s.ocr_text, (row, s.ocr_text)
    assert "Zone 4 - hydrant 112 - high" in s.numeric_labels
    assert any("recognition errors" in w for w in s.warnings)
    assert ex.slide(2).ocr_text == ""  # slides with real text are not OCR'd
    text = deck_text(ex, ex.slides, AnalysisOptions(), set())
    assert "OCR TEXT (may contain recognition errors):" in text


@pytest.mark.slow
@pytest.mark.skipif(not HAS_OCR or find_libreoffice() is None, reason="Tesseract or LibreOffice not installed")
def test_ocr_recovers_text_from_image_slide_pptx(deck_dir, settings):
    ex = extract_deck((deck_dir / "deck.pptx").read_bytes(), "deck.pptx", settings, ocr=True)
    for row in ROWS:
        assert row in ex.slide(7).ocr_text, (row, ex.slide(7).ocr_text)
