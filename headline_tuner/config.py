"""Runtime settings loaded from environment variables, a local .env file, or
Streamlit secrets. Credentials are never hard-coded or logged."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .errors import ConfigurationError, MissingAPIKeyError

PROJECT_ROOT = Path(__file__).resolve().parent.parent

try:  # optional: python-dotenv
    from dotenv import load_dotenv

    load_dotenv(PROJECT_ROOT / ".env", override=False)
except ImportError:  # pragma: no cover
    pass

VALID_EFFORTS = {"low", "medium", "high", "xhigh", "max"}


def _lookup(name: str, secrets: Mapping[str, Any] | None) -> str | None:
    value = os.environ.get(name)
    if value not in (None, ""):
        return value
    if secrets is not None:
        try:
            secret = secrets.get(name)  # type: ignore[union-attr]
        except Exception:  # Streamlit raises if no secrets file exists
            secret = None
        if secret not in (None, ""):
            return str(secret)
    return None


def _int(name: str, default: int, secrets: Mapping[str, Any] | None, minimum: int = 1) -> int:
    raw = _lookup(name, secrets)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigurationError(f"{name} must be a whole number (got an invalid value).") from exc
    if value < minimum:
        raise ConfigurationError(f"{name} must be at least {minimum}.")
    return value


@dataclass(frozen=True)
class Settings:
    model: str | None
    effort: str | None
    thinking: str  # "adaptive" or "off" (omit the parameter)
    fallbacks: bool
    max_file_mb: int
    max_slides: int
    batch_size: int
    validation_retries: int
    conversion_timeout_s: int
    request_timeout_s: int
    max_deck_text_chars: int
    libreoffice_path: str | None
    prepared_by: str | None
    # Automated results email (Resend): on whenever RESEND_API_KEY is set.
    resend_api_key: str | None = None
    notify_enabled: bool = True
    notify_to: tuple[str, ...] = ("Info@tencapital.group",)
    notify_from: str = "TEN Capital Headline Tuner <reports@tencapital.group>"
    notify_reply_to: tuple[str, ...] = ()
    notify_subject_prefix: str = "[TEN Capital]"
    notify_timeout_s: float = 30.0
    public_url: str | None = None

    @property
    def notify_available(self) -> bool:
        return self.notify_enabled and bool(self.resend_api_key) and bool(self.notify_to)

    @property
    def hosted(self) -> bool:
        """True when running on Railway (or another host that sets RAILWAY_ENVIRONMENT)."""
        return bool(os.environ.get("RAILWAY_ENVIRONMENT") or os.environ.get("RAILWAY_PROJECT_ID"))

    @property
    def max_file_bytes(self) -> int:
        return self.max_file_mb * 1024 * 1024

    def require_model(self) -> str:
        if not self.model:
            raise ConfigurationError(
                "No model is configured. Set ANTHROPIC_MODEL in your .env file "
                "(see .env.example) or in .streamlit/secrets.toml."
            )
        return self.model


def load_settings(secrets: Mapping[str, Any] | None = None) -> Settings:
    effort = _lookup("ANTHROPIC_EFFORT", secrets)
    if effort is not None:
        effort = effort.strip().lower()
        if effort in {"", "none", "default"}:
            effort = None
        elif effort not in VALID_EFFORTS:
            raise ConfigurationError(
                f"ANTHROPIC_EFFORT must be one of {sorted(VALID_EFFORTS)} or 'default'."
            )
    thinking = (_lookup("ANTHROPIC_THINKING", secrets) or "adaptive").strip().lower()
    if thinking not in {"adaptive", "off"}:
        raise ConfigurationError("ANTHROPIC_THINKING must be 'adaptive' or 'off'.")
    fallbacks_raw = (_lookup("ANTHROPIC_FALLBACKS", secrets) or "default").strip().lower()
    if fallbacks_raw not in {"default", "off"}:
        raise ConfigurationError("ANTHROPIC_FALLBACKS must be 'default' or 'off'.")
    return Settings(
        model=(_lookup("ANTHROPIC_MODEL", secrets) or "").strip() or None,
        effort=effort,
        thinking=thinking,
        fallbacks=fallbacks_raw == "default",
        max_file_mb=_int("MAX_FILE_MB", 50, secrets),
        max_slides=_int("MAX_SLIDES", 80, secrets),
        batch_size=_int("ANALYSIS_BATCH_SIZE", 8, secrets),
        validation_retries=_int("VALIDATION_RETRIES", 2, secrets, minimum=0),
        conversion_timeout_s=_int("CONVERSION_TIMEOUT_SECONDS", 180, secrets),
        request_timeout_s=_int("REQUEST_TIMEOUT_SECONDS", 600, secrets),
        max_deck_text_chars=_int("MAX_DECK_TEXT_CHARS", 400_000, secrets),
        libreoffice_path=_lookup("LIBREOFFICE_PATH", secrets),
        prepared_by=_lookup("REVIEW_PREPARED_BY", secrets),
        resend_api_key=(_lookup("RESEND_API_KEY", secrets) or "").strip() or None,
        notify_enabled=(_lookup("NOTIFY_ENABLED", secrets) or "true").strip().lower() not in {"false", "0", "no", "off"},
        notify_to=_emails(_lookup("NOTIFY_TO", secrets) or "Info@tencapital.group", "NOTIFY_TO"),
        notify_from=(_lookup("NOTIFY_FROM", secrets) or "TEN Capital Headline Tuner <reports@tencapital.group>").strip(),
        notify_reply_to=_emails(_lookup("NOTIFY_REPLY_TO", secrets) or "", "NOTIFY_REPLY_TO"),
        notify_subject_prefix=(_lookup("NOTIFY_SUBJECT_PREFIX", secrets) or "[TEN Capital]").strip(),
        notify_timeout_s=float(_int("NOTIFY_TIMEOUT_SECONDS", 30, secrets)),
        public_url=_public_url(secrets),
    )


def _emails(raw: str, name: str) -> tuple[str, ...]:
    out = tuple(p.strip() for p in raw.split(",") if p.strip())
    for addr in out:
        if "@" not in addr or " " in addr:
            raise ConfigurationError(f"{name} contains an invalid email address.")
    return out


def _public_url(secrets: Mapping[str, Any] | None) -> str | None:
    url = _lookup("APP_PUBLIC_URL", secrets)
    if url:
        return url.strip()
    domain = _lookup("RAILWAY_PUBLIC_DOMAIN", secrets)
    return f"https://{domain.strip()}" if domain else None


def get_api_key(secrets: Mapping[str, Any] | None = None) -> str:
    """Return ANTHROPIC_API_KEY from the environment or Streamlit secrets."""
    key = _lookup("ANTHROPIC_API_KEY", secrets)
    if key and ("your-" in key.lower() or key.strip().lower().endswith("-here")):
        key = None  # unedited placeholder from .env.example
    if not key:
        raise MissingAPIKeyError(
            "ANTHROPIC_API_KEY is not set. Copy .env.example to .env and add your key, "
            "or set the ANTHROPIC_API_KEY environment variable (or add it to "
            ".streamlit/secrets.toml). Extraction and review editing work without a key; "
            "analysis does not."
        )
    return key.strip()


def has_api_key(secrets: Mapping[str, Any] | None = None) -> bool:
    try:
        get_api_key(secrets)
        return True
    except MissingAPIKeyError:
        return False
