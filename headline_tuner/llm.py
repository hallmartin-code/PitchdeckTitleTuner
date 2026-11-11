"""Thin wrapper around the official Anthropic Python SDK.

Requests use structured outputs (``output_config.format`` with a JSON schema),
adaptive thinking, and streaming (``get_final_message``) so long decks do not
hit HTTP timeouts. The model comes from configuration, never from code.
Deck content and credentials are never logged.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Protocol

from .config import Settings
from .errors import AnalysisError, LLMServiceError

log = logging.getLogger(__name__)

FALLBACK_BETA = "server-side-fallback-2026-07-01"


class MalformedResponseError(AnalysisError):
    """The model returned output that cannot be used (retryable)."""


@dataclass
class LLMResult:
    text: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0


class LLMClient(Protocol):
    model_name: str

    def generate_json(
        self, *, system: str, content: list[dict[str, Any]], schema: dict[str, Any], max_tokens: int
    ) -> LLMResult: ...


class AnthropicLLM:
    def __init__(self, api_key: str, settings: Settings, client: Any | None = None):
        import anthropic

        self._anthropic = anthropic
        self.settings = settings
        self.model_name = settings.require_model()
        self.client = client or anthropic.Anthropic(
            api_key=api_key, timeout=float(settings.request_timeout_s), max_retries=3
        )

    def build_request(
        self, *, system: str, content: list[dict[str, Any]], schema: dict[str, Any], max_tokens: int
    ) -> dict[str, Any]:
        output_config: dict[str, Any] = {"format": {"type": "json_schema", "schema": schema}}
        if self.settings.effort:
            output_config["effort"] = self.settings.effort
        kwargs: dict[str, Any] = {
            "model": self.model_name,
            "max_tokens": max_tokens,
            # Stable system prompt is cached; the deck block in `content` carries its own breakpoint.
            "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            "messages": [{"role": "user", "content": content}],
            "output_config": output_config,
        }
        if self.settings.thinking == "adaptive":
            kwargs["thinking"] = {"type": "adaptive"}
        if self.settings.fallbacks:
            kwargs["betas"] = [FALLBACK_BETA]
            kwargs["fallbacks"] = "default"
        return kwargs

    def generate_json(
        self, *, system: str, content: list[dict[str, Any]], schema: dict[str, Any], max_tokens: int
    ) -> LLMResult:
        a = self._anthropic
        kwargs = self.build_request(system=system, content=content, schema=schema, max_tokens=max_tokens)
        stream_fn = self.client.beta.messages.stream if self.settings.fallbacks else self.client.messages.stream
        try:
            with stream_fn(**kwargs) as stream:
                message = stream.get_final_message()
        except a.AuthenticationError as exc:
            raise LLMServiceError("The Anthropic API rejected the API key. Check ANTHROPIC_API_KEY.") from exc
        except a.PermissionDeniedError as exc:
            raise LLMServiceError(
                "The API key does not have permission to use the configured model "
                f"({self.model_name}). Check your Anthropic Console access or ANTHROPIC_MODEL."
            ) from exc
        except a.NotFoundError as exc:
            raise LLMServiceError(
                f"The configured model '{self.model_name}' was not found. Check ANTHROPIC_MODEL."
            ) from exc
        except a.RateLimitError as exc:
            raise LLMServiceError(
                "The Anthropic API rate limit was reached. Wait a minute and click Analyze again.",
                retryable=True,
            ) from exc
        except a.BadRequestError as exc:
            detail = getattr(exc, "message", "") or ""
            hint = ""
            if "fallback" in detail.lower():
                hint = " The configured model may not support server-side fallbacks; set ANTHROPIC_FALLBACKS=off."
            elif "thinking" in detail.lower():
                hint = " The configured model may not support adaptive thinking; set ANTHROPIC_THINKING=off."
            elif "effort" in detail.lower():
                hint = " The configured model may not support ANTHROPIC_EFFORT; set it to 'default'."
            elif "too long" in detail.lower() or "context" in detail.lower():
                hint = " The deck is too large for one request; reduce ANALYSIS_BATCH_SIZE or MAX_DECK_TEXT_CHARS."
            raise LLMServiceError(f"The Anthropic API rejected the request: {detail[:300]}{hint}") from exc
        except a.APIStatusError as exc:
            retry = exc.status_code >= 500
            raise LLMServiceError(
                f"The Anthropic API returned an error (HTTP {exc.status_code}). "
                + ("Please try again shortly." if retry else "Check your configuration."),
                retryable=retry,
            ) from exc
        except a.APITimeoutError as exc:
            raise LLMServiceError(
                "The request to the Anthropic API timed out. Try again, or lower ANALYSIS_BATCH_SIZE.",
                retryable=True,
            ) from exc
        except a.APIConnectionError as exc:
            raise LLMServiceError(
                "Could not reach the Anthropic API. Check your internet connection or proxy settings.",
                retryable=True,
            ) from exc

        stop = getattr(message, "stop_reason", None)
        if stop == "refusal":
            raise LLMServiceError(
                "The model declined to analyze this content. Review the deck for unusual content and try again."
            )
        text = "".join(getattr(b, "text", "") for b in message.content if getattr(b, "type", "") == "text")
        if stop == "max_tokens":
            raise MalformedResponseError("The response was cut off before it was complete (max_tokens reached).")
        usage = getattr(message, "usage", None)
        result = LLMResult(
            text=text,
            model=getattr(message, "model", self.model_name) or self.model_name,
            input_tokens=getattr(usage, "input_tokens", 0) or 0,
            output_tokens=getattr(usage, "output_tokens", 0) or 0,
            cache_read_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
        )
        log.info(
            "Model call complete: in=%s out=%s cache_read=%s",
            result.input_tokens, result.output_tokens, result.cache_read_tokens,
        )
        return result
