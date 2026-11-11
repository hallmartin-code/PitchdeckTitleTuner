import io
import zipfile

import pytest

from headline_tuner.errors import (
    CorruptFileError,
    EmptyDeckError,
    EncryptedFileError,
    FileTooLargeError,
    UnsupportedFileError,
)
from headline_tuner.extraction import extract_deck
from headline_tuner.filetypes import OLE2_MAGIC, detect_format


def _zip(names):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for n in names:
            zf.writestr(n, "x")
    return buf.getvalue()


def test_detects_real_formats(deck_dir):
    assert detect_format((deck_dir / "deck.pdf").read_bytes()) == "pdf"
    assert detect_format((deck_dir / "deck.pptx").read_bytes()) == "pptx"


def test_legacy_ppt_detected_from_ole_stream_name():
    data = OLE2_MAGIC + b"\x00" * 100 + "PowerPoint Document".encode("utf-16-le") + b"\x00" * 100
    assert detect_format(data) == "ppt"


@pytest.mark.parametrize(
    "data, error, fragment",
    [
        (b"", EmptyDeckError, "empty"),
        (b"\x00\x00   ", EmptyDeckError, "empty"),
        (b"hello, this is plain text", UnsupportedFileError, "not recognised"),
        (_zip(["word/document.xml", "[Content_Types].xml"]), UnsupportedFileError, "Word"),
        (_zip(["random.txt"]), UnsupportedFileError, "not a PowerPoint"),
        (b"PK\x03\x04" + b"garbage" * 20, CorruptFileError, "damaged"),
        (OLE2_MAGIC + b"\x00" * 64, UnsupportedFileError, "not a PowerPoint"),
        (OLE2_MAGIC + "EncryptionInfo".encode("utf-16-le"), EncryptedFileError, "password"),
    ],
)
def test_rejects_bad_files_with_actionable_messages(data, error, fragment):
    with pytest.raises(error) as exc:
        detect_format(data)
    assert fragment.lower() in exc.value.user_message.lower()


def test_format_comes_from_bytes_not_extension(deck_dir, settings):
    data = (deck_dir / "deck.pdf").read_bytes()
    ex = extract_deck(data, "misnamed.pptx", settings, render=False)
    assert ex.file_format == "pdf"
    assert any("extension does not match" in w for w in ex.warnings)


def test_file_size_limit(settings, monkeypatch):
    monkeypatch.setenv("MAX_FILE_MB", "1")
    from headline_tuner.config import load_settings

    small = load_settings()
    with pytest.raises(FileTooLargeError):
        extract_deck(b"%PDF-1.4" + b"0" * (2 * 1024 * 1024), "big.pdf", small)
