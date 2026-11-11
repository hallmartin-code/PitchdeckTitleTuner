from types import SimpleNamespace

import anthropic
import pytest

from headline_tuner.config import get_api_key, has_api_key, load_settings
from headline_tuner.errors import ConfigurationError, LLMServiceError, MissingAPIKeyError
from headline_tuner.llm import FALLBACK_BETA, AnthropicLLM, MalformedResponseError
from headline_tuner.prompts import BATCH_SCHEMA

try:  # the SDK's HTTP library (httpx2 in anthropic>=1.0)
    import httpx2 as httpx
except ImportError:  # pragma: no cover
    import httpx


def test_missing_api_key_message():
    assert not has_api_key()
    with pytest.raises(MissingAPIKeyError) as exc:
        get_api_key()
    assert ".env.example" in exc.value.user_message and "secrets.toml" in exc.value.user_message


def test_api_key_from_env_or_secrets(monkeypatch):
    assert get_api_key({"ANTHROPIC_API_KEY": "sk-from-secrets"}) == "sk-from-secrets"
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-from-env")
    assert get_api_key({"ANTHROPIC_API_KEY": "sk-from-secrets"}) == "sk-from-env"


def test_model_must_be_configured(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_MODEL")
    with pytest.raises(ConfigurationError, match="ANTHROPIC_MODEL"):
        AnthropicLLM("sk-test", load_settings(), client=object())


def test_invalid_config_values(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_EFFORT", "extreme")
    with pytest.raises(ConfigurationError):
        load_settings()


class _Stream:
    def __init__(self, message=None, error=None):
        self.message, self.error = message, error

    def __enter__(self):
        if self.error:
            raise self.error
        return self

    def __exit__(self, *a):
        return False

    def get_final_message(self):
        return self.message


def _client(message=None, error=None):
    seen = {}

    def stream(**kwargs):
        seen["kwargs"] = kwargs
        return _Stream(message, error)

    client = SimpleNamespace(messages=SimpleNamespace(stream=stream), beta=SimpleNamespace(messages=SimpleNamespace(stream=stream)))
    return client, seen


def _message(text='{"reviews": []}', stop="end_turn"):
    return SimpleNamespace(
        content=[SimpleNamespace(type="thinking", thinking=""), SimpleNamespace(type="text", text=text)],
        stop_reason=stop, model="test-model",
        usage=SimpleNamespace(input_tokens=10, output_tokens=5, cache_read_input_tokens=3),
    )


def _call(llm):
    return llm.generate_json(system="sys", content=[{"type": "text", "text": "hi"}], schema=BATCH_SCHEMA, max_tokens=1000)


def test_request_shape_with_defaults(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_EFFORT", "high")
    client, seen = _client(_message())
    result = _call(AnthropicLLM("sk-test", load_settings(), client=client))
    kw = seen["kwargs"]
    assert kw["model"] == "test-model"
    assert kw["output_config"] == {"format": {"type": "json_schema", "schema": BATCH_SCHEMA}, "effort": "high"}
    assert kw["thinking"] == {"type": "adaptive"}
    assert kw["betas"] == [FALLBACK_BETA] and kw["fallbacks"] == "default"
    assert kw["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert result.text == '{"reviews": []}' and result.cache_read_tokens == 3


def test_request_shape_with_optional_features_off(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_FALLBACKS", "off")
    monkeypatch.setenv("ANTHROPIC_THINKING", "off")
    client, seen = _client(_message())
    _call(AnthropicLLM("sk-test", load_settings(), client=client))
    kw = seen["kwargs"]
    assert "betas" not in kw and "fallbacks" not in kw and "thinking" not in kw
    assert "effort" not in kw["output_config"]


def test_refusal_and_truncation():
    client, _ = _client(_message(stop="refusal"))
    with pytest.raises(LLMServiceError, match="declined"):
        _call(AnthropicLLM("sk", load_settings(), client=client))
    client, _ = _client(_message(stop="max_tokens"))
    with pytest.raises(MalformedResponseError):
        _call(AnthropicLLM("sk", load_settings(), client=client))


def _req():
    return httpx.Request("POST", "https://api.anthropic.com/v1/messages")


@pytest.mark.parametrize(
    "error, fragment, retryable",
    [
        (lambda: anthropic.AuthenticationError("bad", response=httpx.Response(401, request=_req()), body=None), "API key", False),
        (lambda: anthropic.NotFoundError("nf", response=httpx.Response(404, request=_req()), body=None), "ANTHROPIC_MODEL", False),
        (lambda: anthropic.RateLimitError("rl", response=httpx.Response(429, request=_req()), body=None), "rate limit", True),
        (lambda: anthropic.InternalServerError("ise", response=httpx.Response(500, request=_req()), body=None), "HTTP 500", True),
        (lambda: anthropic.APIConnectionError(request=_req()), "internet connection", True),
    ],
)
def test_sdk_errors_become_actionable_messages(error, fragment, retryable):
    client, _ = _client(error=error())
    with pytest.raises(LLMServiceError) as exc:
        _call(AnthropicLLM("sk", load_settings(), client=client))
    assert fragment in exc.value.user_message and exc.value.retryable is retryable
    assert "sk" not in exc.value.user_message.split()


def test_placeholder_key_from_env_example_is_not_a_key(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "your-anthropic-api-key-here")
    assert not has_api_key()
