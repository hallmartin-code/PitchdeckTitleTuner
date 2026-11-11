"""Detect the real format of an uploaded file from its bytes, not its extension."""

from __future__ import annotations

import io
import zipfile
from typing import Literal

from .errors import EmptyDeckError, EncryptedFileError, CorruptFileError, UnsupportedFileError

DeckFormat = Literal["pdf", "pptx", "ppt"]

OLE2_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
ZIP_MAGIC = b"PK\x03\x04"
_PPT_STREAM = "PowerPoint Document".encode("utf-16-le")
_ENCRYPTION_INFO = "EncryptionInfo".encode("utf-16-le")
_ENCRYPTED_PACKAGE = "EncryptedPackage".encode("utf-16-le")

# Guard against zip bombs: total uncompressed size of a PPTX package.
MAX_UNCOMPRESSED_BYTES = 1024 * 1024 * 1024  # 1 GiB

SUPPORTED_HINT = "Upload a PDF, PowerPoint (.pptx), or legacy PowerPoint (.ppt) file."


def detect_format(data: bytes) -> DeckFormat:
    if not data or not data.strip(b"\x00 \r\n\t"):
        raise EmptyDeckError("The uploaded file is empty.")

    if b"%PDF-" in data[:1024]:
        return "pdf"

    if data.startswith(ZIP_MAGIC):
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as zf:
                names = set(zf.namelist())
                total = sum(i.file_size for i in zf.infolist())
        except zipfile.BadZipFile as exc:
            raise CorruptFileError(
                "The file looks like a PowerPoint package but is damaged and cannot be opened."
            ) from exc
        if total > MAX_UNCOMPRESSED_BYTES:
            raise UnsupportedFileError(
                "The file expands to an unreasonably large size and was rejected for safety."
            )
        if "ppt/presentation.xml" in names:
            return "pptx"
        if any(n.startswith("word/") for n in names):
            raise UnsupportedFileError(f"This is a Word document, not a pitch deck. {SUPPORTED_HINT}")
        if any(n.startswith("xl/") for n in names):
            raise UnsupportedFileError(f"This is an Excel workbook, not a pitch deck. {SUPPORTED_HINT}")
        raise UnsupportedFileError(f"This ZIP file is not a PowerPoint presentation. {SUPPORTED_HINT}")

    if data.startswith(OLE2_MAGIC):
        if _ENCRYPTION_INFO in data or _ENCRYPTED_PACKAGE in data:
            raise EncryptedFileError(
                "This presentation is password-protected. Remove the password in PowerPoint "
                "(File > Info > Protect Presentation) and upload it again."
            )
        if _PPT_STREAM in data:
            return "ppt"
        raise UnsupportedFileError(
            f"This is a legacy Office file but not a PowerPoint presentation. {SUPPORTED_HINT}"
        )

    raise UnsupportedFileError(f"The file format was not recognised. {SUPPORTED_HINT}")


def extension_matches(filename: str, fmt: DeckFormat) -> bool:
    return filename.lower().rsplit(".", 1)[-1] == fmt if "." in filename else False
