"""User-facing error types. Every error carries a message safe to show in the UI.

Messages never include deck content or credentials.
"""

from __future__ import annotations


class HeadlineTunerError(Exception):
    """Base class for errors with an actionable, user-facing message."""

    def __init__(self, message: str):
        super().__init__(message)
        self.user_message = message


# --- configuration -------------------------------------------------------


class ConfigurationError(HeadlineTunerError):
    pass


class MissingAPIKeyError(ConfigurationError):
    pass


# --- input files ---------------------------------------------------------


class DeckError(HeadlineTunerError):
    """Problems with the uploaded file itself."""


class EmptyDeckError(DeckError):
    pass


class UnsupportedFileError(DeckError):
    pass


class CorruptFileError(DeckError):
    pass


class EncryptedFileError(DeckError):
    pass


class FileTooLargeError(DeckError):
    pass


class TooManySlidesError(DeckError):
    pass


# --- conversion ----------------------------------------------------------


class ConversionUnavailableError(HeadlineTunerError):
    pass


class ConversionFailedError(HeadlineTunerError):
    pass


# --- analysis ------------------------------------------------------------


class AnalysisError(HeadlineTunerError):
    """The analysis could not be completed (validation failed after retries, etc.)."""


class LLMServiceError(AnalysisError):
    """The Anthropic API call failed."""

    def __init__(self, message: str, retryable: bool = False):
        super().__init__(message)
        self.retryable = retryable


# --- PDF -----------------------------------------------------------------


class LayoutOverflowError(HeadlineTunerError):
    """The selected content does not fit on one page at the minimum font size."""

    def __init__(self, message: str, requested_rows: int, max_fitting_rows: int):
        super().__init__(message)
        self.requested_rows = requested_rows
        self.max_fitting_rows = max_fitting_rows
