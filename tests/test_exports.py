import json

from headline_tuner.config import load_settings
from headline_tuner.exports import to_html, to_json
from headline_tuner.extraction import extract_deck
from headline_tuner.models import DeckAnalysis
from headline_tuner.synthetic import sample_analysis


def test_json_round_trip_and_no_images(deck_dir):
    ex = extract_deck((deck_dir / "deck.pdf").read_bytes(), "deck.pdf", load_settings())
    a = sample_analysis()
    data = json.loads(to_json(a, ex))
    again = DeckAnalysis.model_validate(data["analysis"])
    assert [r.slide_number for r in again.slide_reviews] == [r.slide_number for r in a.slide_reviews]
    assert [l["slide_number"] for l in data["main_deck_narrative"]] == list(range(1, 10))
    assert all("thumbnail_png" not in s for s in data["extraction"]["slides"])
    assert data["extraction"]["slides"][4]["text"][0] == "Forecast: $5.2M revenue in FY2028 (projected, not actual)"


def test_html_contains_all_sections_and_escapes_content():
    a = sample_analysis()
    a.review(3).recommended_headline = "Sensors <script>alert('x')</script> help crews."
    html = to_html(a)
    assert "<script>alert" not in html and "&lt;script&gt;" in html
    for section in ["Narrative assessment", "Main-deck narrative", "Priority improvements", "Appendix review",
                    "Extraction limitations", "Conflicting figures"]:
        assert section in html
    for r in a.slide_reviews:
        assert f'id="slide-{r.slide_number}"' in html
