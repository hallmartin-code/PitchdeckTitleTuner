"""End-to-end UI test of the Streamlit app with a mocked model (no network)."""

import json

import pytest
from conftest import FakeLLM
from streamlit.testing.v1 import AppTest

import headline_tuner.analyzer as analyzer_mod
import headline_tuner.llm as llm_mod
from conftest import ROOT


@pytest.fixture
def fake_llm(monkeypatch):
    fake = FakeLLM()

    class _Factory:
        def __new__(cls, api_key, settings):
            assert api_key == "sk-test-not-real"
            return fake

    monkeypatch.setattr(llm_mod, "AnthropicLLM", _Factory)
    return fake


def _app(deck_dir):
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=120)
    at.session_state["_test_upload"] = ("tidewater.pdf", (deck_dir / "deck.pdf").read_bytes())
    return at


def test_app_without_api_key_blocks_analysis(deck_dir):
    at = _app(deck_dir).run()
    assert not at.exception
    assert any("ANTHROPIC_API_KEY" in str(c.value) or "API key" in str(c.value) for c in at.sidebar.markdown)
    analyze = next(b for b in at.button if b.label == "Analyze deck")
    assert analyze.disabled
    # extraction still works and titles are editable
    titles = [t.value for t in at.text_input]
    assert "The Problem" in titles and len(titles) == 9


def test_full_flow_with_mocked_model(deck_dir, monkeypatch, fake_llm):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-not-real")
    at = _app(deck_dir).run()
    assert not at.exception
    info = " ".join(str(i.value) for i in at.info)
    assert "Privacy notice" in info and "Anthropic API" in info and "Nothing is sent before you click" in info
    assert fake_llm.calls == []  # nothing sent before the explicit action

    # user corrects a title
    at.text_input[1].set_value("Water loss problem").run()
    next(b for b in at.button if b.label == "Analyze deck").click().run()
    assert not at.exception, at.exception
    assert len(fake_llm.batch_calls) == 2  # 9 slides, batch size 8
    assert "CURRENT TITLE: Water loss problem" in fake_llm.calls[0]["text"]

    # results rendered, PDF previewed, downloads offered
    assert any("Narrative assessment" in str(m.value) for m in at.markdown)
    labels = [b.label for b in at.get("download_button")]
    assert {"Download one-page PDF", "Download full review (HTML)", "Download full analysis (JSON)"} <= set(labels)

    # clicking again with identical inputs uses the session cache (no new API calls)
    calls = len(fake_llm.calls)
    next(b for b in at.button if b.label == "Analyze deck").click().run()
    assert len(fake_llm.calls) == calls
    assert any("cached result" in str(s.value) for s in at.success)


def _capture_emails(monkeypatch):
    import headline_tuner.notify as notify_mod

    sent = []

    def transport(method, url, headers, body, timeout):
        sent.append({"url": url, "headers": headers, "payload": json.loads(body)})
        return 200, b'{"id": "email_123"}'

    monkeypatch.setattr(notify_mod, "urllib_transport", transport)
    return sent


def test_results_emailed_automatically_once_per_analysis(deck_dir, monkeypatch, fake_llm):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-not-real")
    monkeypatch.setenv("RESEND_API_KEY", "re_test_not_real")
    sent = _capture_emails(monkeypatch)
    at = _app(deck_dir).run()
    assert any("emailed automatically via Resend to Info@tencapital.group" in str(i.value) for i in at.info)
    assert sent == []  # nothing is emailed before the analysis runs

    next(b for b in at.button if b.label == "Analyze deck").click().run()
    assert not at.exception, at.exception
    assert len(sent) == 1
    payload = sent[0]["payload"]
    assert payload["to"] == ["Info@tencapital.group"]
    assert payload["from"] == "TEN Capital Headline Tuner <reports@tencapital.group>"
    names = [a["filename"] for a in payload["attachments"]]
    assert names == ["tidewater_headline_review.pdf", "tidewater_full_review.html", "tidewater_analysis.json"]
    assert sent[0]["headers"]["Authorization"] == "Bearer re_test_not_real"
    assert any("Results emailed to Info@tencapital.group" in str(s.value) for s in at.success)

    # cached re-click: no new analysis, no new email
    next(b for b in at.button if b.label == "Analyze deck").click().run()
    assert len(sent) == 1

    # an edited PDF can be sent on request, once
    at.multiselect[0].set_value([1, 2]).run()
    next(b for b in at.button if b.label.startswith("Email this version")).click().run()
    assert len(sent) == 2
    assert [a["filename"] for a in sent[1]["payload"]["attachments"]][0] == "tidewater_headline_review.pdf"
    at.run()  # next rerun: the button is replaced by a confirmation, so it cannot be sent twice
    assert any("This version of the PDF has been emailed" in str(c.value) for c in at.caption)
    assert not any(b.label.startswith("Email this version") for b in at.button)
    assert len(sent) == 2


def test_no_email_without_resend_key(deck_dir, monkeypatch, fake_llm):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-not-real")
    sent = _capture_emails(monkeypatch)
    at = _app(deck_dir).run()
    next(b for b in at.button if b.label == "Analyze deck").click().run()
    assert not at.exception and sent == []
    assert not any(b.label.startswith("Email this version") for b in at.button)
    assert any("Results email off" in str(m.value) for m in at.sidebar.markdown)


def test_no_password_gate(deck_dir, monkeypatch):
    monkeypatch.setenv("TEN_APP_PASSWORD", "ignored")
    at = _app(deck_dir).run()
    assert not any(t.label == "Access password" for t in at.text_input)
    assert any(t.value == "The Problem" for t in at.text_input)


def test_branded_landing_page(deck_dir):
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=60).run()
    assert not at.exception
    html = " ".join(str(m.value) for m in at.markdown)
    for part in ["TEN CAPITAL".title(), "Deck Analyzer", "Headline Review", "Powered by TEN Capital Network",
                 "Deck content is sent to Anthropic only when you click Analyze deck"]:
        assert part.lower() in html.lower(), part
    assert "gmail" not in html.lower()


def test_favicon_asset_is_present_and_configured():
    from PIL import Image

    png = ROOT / "static" / "favicon.png"
    with Image.open(png) as im:
        assert im.format == "PNG" and im.size[0] == im.size[1] >= 32
    assert (ROOT / "static" / "favicon.ico").is_file()
    assert "page_icon=" in (ROOT / "app.py").read_text(encoding="utf-8")
    assert "enableStaticServing = true" in (ROOT / ".streamlit" / "config.toml").read_text(encoding="utf-8")
