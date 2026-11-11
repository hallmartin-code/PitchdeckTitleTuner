import subprocess

import pytest

from headline_tuner import convert, extraction
from headline_tuner.config import load_settings
from headline_tuner.convert import convert_with_libreoffice, find_libreoffice
from headline_tuner.errors import ConversionFailedError, ConversionUnavailableError
from headline_tuner.extraction import extract_deck
from headline_tuner.filetypes import OLE2_MAGIC

FAKE_PPT = OLE2_MAGIC + b"\x00" * 200 + "PowerPoint Document".encode("utf-16-le") + b"\x00" * 200


def test_configured_path_that_does_not_exist(tmp_path):
    assert find_libreoffice(str(tmp_path / "nope" / "soffice")) is None


def test_conversion_without_libreoffice_explains_install(tmp_path):
    with pytest.raises(ConversionUnavailableError) as exc:
        convert_with_libreoffice(tmp_path / "a.ppt", tmp_path, "pptx", soffice=None, timeout_s=5)
    msg = exc.value.user_message
    assert "winget install" in msg and "brew install --cask libreoffice" in msg and "LIBREOFFICE_PATH" in msg


def test_legacy_ppt_without_libreoffice(monkeypatch, settings):
    monkeypatch.setattr(extraction, "find_libreoffice", lambda _p=None: None)
    with pytest.raises(ConversionUnavailableError) as exc:
        extract_deck(FAKE_PPT, "old.ppt", settings)
    assert ".pptx" in exc.value.user_message and "LibreOffice" in exc.value.user_message


def test_pptx_without_libreoffice_still_extracts(monkeypatch, settings, deck_dir):
    monkeypatch.setattr(extraction, "find_libreoffice", lambda _p=None: None)
    ex = extract_deck((deck_dir / "deck.pptx").read_bytes(), "deck.pptx", settings)
    assert ex.total_slides == 11 and not ex.rendering_available
    assert any("thumbnails are unavailable" in w for w in ex.warnings)
    assert any("Image-based slides could not be reviewed visually" in w and "7" in w for w in ex.warnings)


def test_conversion_uses_argument_list_and_timeout(monkeypatch, tmp_path):
    seen = {}

    def fake_run(cmd, **kwargs):
        seen["cmd"], seen["kwargs"] = cmd, kwargs
        raise subprocess.TimeoutExpired(cmd, kwargs["timeout"])

    monkeypatch.setattr(convert.subprocess, "run", fake_run)
    src = tmp_path / "deck.pptx"
    src.write_bytes(b"x")
    with pytest.raises(ConversionFailedError) as exc:
        convert_with_libreoffice(src, tmp_path / "out", "pdf", soffice="soffice", timeout_s=7)
    assert isinstance(seen["cmd"], list) and seen["kwargs"]["timeout"] == 7
    assert "shell" not in seen["kwargs"]
    assert "7 seconds" in exc.value.user_message


def test_failed_conversion_reports_actionable_error(monkeypatch, tmp_path):
    monkeypatch.setattr(
        convert.subprocess, "run", lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, b"", b"err")
    )
    src = tmp_path / "deck.ppt"
    src.write_bytes(b"x")
    with pytest.raises(ConversionFailedError) as exc:
        convert_with_libreoffice(src, tmp_path / "out", "pptx", soffice="soffice", timeout_s=7)
    assert "save as .pptx" in exc.value.user_message


SOFFICE = find_libreoffice()


@pytest.mark.slow
@pytest.mark.skipif(SOFFICE is None, reason="LibreOffice not installed")
def test_real_legacy_ppt_round_trip(deck_dir, tmp_path):
    """Integration: convert the synthetic PPTX to legacy .ppt, then extract it."""
    out = tmp_path / "ppt"
    profile = tmp_path / "profile"
    subprocess.run(
        [SOFFICE, f"-env:UserInstallation={profile.resolve().as_uri()}", "--headless", "--convert-to", "ppt",
         "--outdir", str(out), str(deck_dir / "deck.pptx")],
        capture_output=True, timeout=180, check=True,
    )
    data = (out / "deck.ppt").read_bytes()
    ex = extract_deck(data, "deck.ppt", load_settings(), render=False)
    assert ex.file_format == "ppt"
    assert [s.number for s in ex.slides] == list(range(1, 12))
    assert ex.slide(2).title == "The Problem"
    assert any("Converted from legacy .ppt" in w for w in ex.warnings)


@pytest.mark.slow
@pytest.mark.skipif(SOFFICE is None, reason="LibreOffice not installed")
def test_real_pptx_thumbnails(deck_dir, settings):
    ex = extract_deck((deck_dir / "deck.pptx").read_bytes(), "deck.pptx", settings)
    assert ex.rendering_available
    assert all(s.thumbnail_png for s in ex.slides if not s.hidden)
    assert ex.slide(10).thumbnail_png is None  # hidden slide is not rendered by LibreOffice
