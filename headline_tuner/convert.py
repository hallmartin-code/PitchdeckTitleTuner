"""LibreOffice-based conversion (legacy PPT -> PPTX, PowerPoint -> PDF for rendering).

Always uses subprocess argument lists (no shell) and a timeout, and runs with
an isolated, temporary LibreOffice profile so it does not collide with a
LibreOffice window the user already has open.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from .errors import ConversionFailedError, ConversionUnavailableError

INSTALL_INSTRUCTIONS = (
    "Install LibreOffice to enable this: on Windows run `winget install "
    "TheDocumentFoundation.LibreOffice` (or download it from https://www.libreoffice.org), "
    "on macOS run `brew install --cask libreoffice`, and on Debian/Ubuntu run "
    "`sudo apt install libreoffice-impress`. If it is installed in a non-standard location, "
    "set LIBREOFFICE_PATH to the soffice executable."
)

_CANDIDATES_WINDOWS = [
    r"C:\Program Files\LibreOffice\program\soffice.exe",
    r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
]
_CANDIDATES_MAC = ["/Applications/LibreOffice.app/Contents/MacOS/soffice"]
_CANDIDATES_LINUX = ["/usr/bin/soffice", "/usr/lib/libreoffice/program/soffice", "/snap/bin/libreoffice"]


def find_libreoffice(configured_path: str | None = None) -> str | None:
    if configured_path:
        p = Path(configured_path)
        return str(p) if p.is_file() else None
    for name in ("soffice", "libreoffice"):
        found = shutil.which(name)
        if found:
            return found
    if sys.platform.startswith("win"):
        candidates = _CANDIDATES_WINDOWS
    elif sys.platform == "darwin":
        candidates = _CANDIDATES_MAC
    else:
        candidates = _CANDIDATES_LINUX
    for c in candidates:
        if Path(c).is_file():
            return c
    return None


def convert_with_libreoffice(
    src: Path,
    out_dir: Path,
    target: str,
    *,
    soffice: str | None,
    timeout_s: int,
) -> Path:
    """Convert ``src`` to ``target`` ("pdf" or "pptx") inside ``out_dir``."""
    if not soffice:
        raise ConversionUnavailableError(f"LibreOffice was not found. {INSTALL_INSTRUCTIONS}")
    if target not in {"pdf", "pptx"}:
        raise ValueError(f"Unsupported conversion target: {target}")
    out_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="headline_tuner_lo_profile_") as profile:
        cmd = [
            soffice,
            f"-env:UserInstallation={Path(profile).resolve().as_uri()}",
            "--headless",
            "--invisible",
            "--norestore",
            "--nolockcheck",
            "--nodefault",
            "--convert-to",
            target,
            "--outdir",
            str(out_dir),
            str(src),
        ]
        env = dict(os.environ)
        env.pop("PYTHONPATH", None)  # LibreOffice bundles its own Python
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                timeout=timeout_s,
                check=False,
                env=env,
                stdin=subprocess.DEVNULL,
            )
        except subprocess.TimeoutExpired as exc:
            raise ConversionFailedError(
                f"LibreOffice did not finish converting the file within {timeout_s} seconds. "
                "Try exporting the deck to PDF yourself and uploading the PDF."
            ) from exc
        except OSError as exc:
            raise ConversionUnavailableError(
                f"LibreOffice could not be started. {INSTALL_INSTRUCTIONS}"
            ) from exc
    expected = out_dir / f"{src.stem}.{target}"
    if proc.returncode != 0 or not expected.is_file() or expected.stat().st_size == 0:
        raise ConversionFailedError(
            "LibreOffice could not convert this file (it may be damaged or password-protected). "
            "Open it in PowerPoint and save as .pptx or export to PDF, then upload that file."
        )
    return expected
