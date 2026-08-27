import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from seezar_operator.scenarios.scenario_3 import _mentions, _top_model, _vehicle_insights


def test_full_name_and_model_word_both_count():
    texts = ["I love the Toyota Camry", "looking at a camry", "CAMRY please"]
    assert _mentions("Toyota Camry", texts) == 3


def test_make_alone_does_not_count():
    """Interest in the brand is not interest in this model."""
    assert _mentions("Toyota Camry", ["do you have any Toyota?"]) == 0


def test_single_token_model_matches_itself():
    assert _mentions("Porsche", ["a Porsche 911", "porsche please", "no match"]) == 2


def test_word_boundary_prevents_substring_hits():
    assert _mentions("Ford", ["I went to Bradford yesterday"]) == 0


def test_empty_model_is_zero():
    assert _mentions("", ["Toyota Camry"]) == 0


def test_vehicle_insights_sorts_by_clicks_descending():
    metrics = {"queryInsights": {"vehicleQueryInsights": {
        "mostQueried": "Chevrolet Captiva",
        "vehicleInsights": [{"name": "Nissan Sunny", "total": 30},
                            {"name": "Toyota Camry", "total": 60}],
    }}}
    most_queried, rows = _vehicle_insights(metrics)
    assert most_queried == "Chevrolet Captiva"
    assert rows == [("Toyota Camry", 60), ("Nissan Sunny", 30)]


def test_top_model_prefers_actual_click_counts_over_mostqueried():
    """The API's mostQueried field disagrees with its own vehicleInsights."""
    assert _top_model("Chevrolet Captiva", [("Toyota Camry", 60), ("Nissan Sunny", 30)]) == "Toyota Camry"


def test_top_model_falls_back_when_no_chart_data():
    assert _top_model("Chevrolet Captiva", []) == "Chevrolet Captiva"


def test_vehicle_insights_handles_missing_sections():
    assert _vehicle_insights({}) == (None, [])
    assert _vehicle_insights({"queryInsights": {}}) == (None, [])
