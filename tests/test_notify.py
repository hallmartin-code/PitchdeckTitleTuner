import base64
import json

import pytest

from headline_tuner.config import load_settings
from headline_tuner.errors import ConfigurationError
from headline_tuner.notify import RESEND_EMAILS_URL, render_email, send_results_email
from headline_tuner.synthetic import sample_analysis

FILES = {"deck_headline_review.pdf": b"%PDF-1.4 fake", "deck_full_review.html": b"<html></html>"}


class Recorder:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, method, url, headers, body, timeout):
        self.calls.append({"method": method, "url": url, "headers": headers, "payload": json.loads(body)})
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


@pytest.fixture
def notify_settings(monkeypatch):
    monkeypatch.setenv("RESEND_API_KEY", "re_test_key")
    return load_settings()


def _send(settings, transport, generation="gen-1"):
    return send_results_email(sample_analysis(), FILES, settings=settings, generation=generation,
                              transport=transport, sleep=lambda s: None)


def test_defaults_target_info_mailbox(notify_settings):
    assert notify_settings.notify_to == ("Info@tencapital.group",)
    assert notify_settings.notify_from == "TEN Capital Headline Tuner <reports@tencapital.group>"
    assert notify_settings.notify_available


def test_successful_send_payload(notify_settings):
    t = Recorder([(200, b'{"id": "msg_1"}')])
    out = _send(notify_settings, t)
    assert out.sent and out.message_id == "msg_1" and out.status_text == "Results emailed to Info@tencapital.group"
    call = t.calls[0]
    assert call["method"] == "POST" and call["url"] == RESEND_EMAILS_URL
    assert call["headers"]["Authorization"] == "Bearer re_test_key"
    assert call["headers"]["Idempotency-Key"] == "headline-tuner/gen-1"
    p = call["payload"]
    assert p["to"] == ["Info@tencapital.group"]
    assert "Tidewater Analytics" in p["subject"] and "[TEN Capital]" in p["subject"]
    assert [a["filename"] for a in p["attachments"]] == list(FILES)
    assert base64.b64decode(p["attachments"][0]["content"]) == FILES["deck_headline_review.pdf"]
    assert "re_test_key" not in json.dumps(p)


def test_email_body_follows_report_template():
    subject, html, text = render_email(sample_analysis(), subject_prefix="[TEN Capital]", attachment_names=["a.pdf"])
    for part in ["PITCH DECK HEADLINE REVIEW", "Narrative assessment", "Recommended headlines", "Priority improvements",
                 "Narrative gaps", "Findings are based solely on the content of the supplied deck"]:
        assert part in html and part.upper() in text.upper(), part
    assert "Clip-on hydrant sensors" in html and "†" in html
    assert "Appendix slides (1)" in html
    assert "10 slides reviewed" in subject


def test_content_is_escaped():
    a = sample_analysis()
    a.review(3).recommended_headline = "<script>alert(1)</script> crews."
    _, html, _ = render_email(a, subject_prefix="", attachment_names=[])
    assert "<script>" not in html and "&lt;script&gt;" in html


def test_no_key_skips_without_calling_resend():
    t = Recorder([])
    out = _send(load_settings(), t)
    assert not out.attempted and "no RESEND_API_KEY" in out.skipped_reason and t.calls == []


def test_disabled_skips(monkeypatch):
    monkeypatch.setenv("RESEND_API_KEY", "re_x")
    monkeypatch.setenv("NOTIFY_ENABLED", "false")
    out = _send(load_settings(), Recorder([]))
    assert "disabled" in out.skipped_reason


def test_client_error_not_retried(notify_settings):
    t = Recorder([(403, b'{"message": "domain not verified"}')])
    out = _send(notify_settings, t)
    assert not out.sent and len(t.calls) == 1 and "sender domain must be verified" in out.error


def test_server_and_network_errors_retried_then_reported(notify_settings):
    t = Recorder([(500, b"oops"), OSError("down"), (502, b"bad gateway")])
    out = _send(notify_settings, t)
    assert not out.sent and len(t.calls) == 3 and "502" in out.error


def test_retry_succeeds(notify_settings):
    t = Recorder([(429, b"{}"), (200, b'{"id": "msg_2"}')])
    assert _send(notify_settings, t).sent and len(t.calls) == 2


def test_recipient_configuration(monkeypatch):
    monkeypatch.setenv("NOTIFY_TO", "a@x.com, b@y.com")
    assert load_settings().notify_to == ("a@x.com", "b@y.com")
    monkeypatch.setenv("NOTIFY_TO", "not-an-email")
    with pytest.raises(ConfigurationError):
        load_settings()


def test_public_url_from_railway(monkeypatch):
    monkeypatch.setenv("RAILWAY_PUBLIC_DOMAIN", "x.up.railway.app")
    assert load_settings().public_url == "https://x.up.railway.app"
