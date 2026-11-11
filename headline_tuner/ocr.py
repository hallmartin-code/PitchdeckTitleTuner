"""Optional OCR fallback for image-based slides using the Tesseract CLI.

The Tesseract executable is called directly (argument list, timeout, temporary
directory). PyMuPDF's built-in OCR wrapper was tested and dropped text inside
boxed regions that the Tesseract CLI reads correctly.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from functools import lru_cache
from pathlib import Path

from .textutils import clean_block

OCR_DPI = 200

INSTALL_HINT = (
    "Install Tesseract to enable OCR: on Windows run `winget install UB-Mannheim.TesseractOCR`, on macOS "
    "`brew install tesseract`, on Debian/Ubuntu `sudo apt install tesseract-ocr`. If it is installed in a "
    "non-standard location, set TESSERACT_PATH to the tesseract executable."
)

_CANDIDATES = {
    "win": [
        r"C:\Program Files\Tesseract-OCR\tesseract.exe",
        r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
        str(Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Tesseract-OCR" / "tesseract.exe"),
    ],
    "darwin": ["/opt/homebrew/bin/tesseract", "/usr/local/bin/tesseract"],
    "linux": ["/usr/bin/tesseract", "/usr/local/bin/tesseract"],
}


@lru_cache(maxsize=4)
def find_tesseract(configured: str | None = None) -> str | None:
    configured = configured or os.environ.get("TESSERACT_PATH")
    if configured:
        return configured if Path(configured).is_file() else None
    found = shutil.which("tesseract")
    if found:
        return found
    key = "win" if sys.platform.startswith("win") else ("darwin" if sys.platform == "darwin" else "linux")
    return next((c for c in _CANDIDATES[key] if Path(c).is_file()), None)


def ocr_png(png: bytes, *, tesseract: str, language: str = "eng", timeout_s: int = 60) -> str:
    """Return OCR text for a PNG image. Raises RuntimeError on failure."""
    with tempfile.TemporaryDirectory(prefix="headline_tuner_ocr_") as tmp:
        img = Path(tmp) / "slide.png"
        img.write_bytes(png)
        try:
            proc = subprocess.run(
                [tesseract, str(img), "stdout", "-l", language, "--psm", "3"],
                capture_output=True,
                timeout=timeout_s,
                check=False,
                stdin=subprocess.DEVNULL,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("OCR timed out") from exc
        except OSError as exc:
            raise RuntimeError("Tesseract could not be started") from exc
    if proc.returncode != 0:
        raise RuntimeError("Tesseract returned an error")
    return clean_block(proc.stdout.decode("utf-8", errors="replace"))
