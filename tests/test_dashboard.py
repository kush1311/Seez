import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from seezar_operator.dashboard import Dashboard


def _dash(table, captured=None):
    """A Dashboard with its caches primed, so no browser is needed."""
    d = Dashboard.__new__(Dashboard)
    d._captured = captured or []
    d._dealer_cache = table
    d._bot_pref = {}
    d.page = None
    return d


TABLE = {
    "Ejner Hessel": {"id": "2", "bots": ["527"]},
    "Norton Way": {"id": "454", "bots": ["300"]},
    "Norton Way Carverse": {"id": "455", "bots": ["301"]},
}


def test_exact_name_wins_over_longer_match():
    assert _dash(TABLE).resolve("Norton Way")[0] == "454"


def test_case_insensitive_substring():
    assert _dash(TABLE).resolve("ejner hessel")[0] == "2"


def test_unknown_name_raises():
    with pytest.raises(LookupError, match="No dealership"):
        _dash(TABLE).resolve("Definitely Not A Dealer")


def test_ambiguous_prefix_raises():
    table = {"Alpha Motors": {"id": "1", "bots": ["9"]},
             "Alpha Cars": {"id": "2", "bots": ["8"]}}
    with pytest.raises(LookupError, match="ambiguous"):
        _dash(table).resolve("Alpha")


def test_single_bot_is_returned_without_navigating():
    """page is None here, so this proves no navigation happened."""
    assert _dash(TABLE).resolve("Ejner Hessel")[1] == "527"


def test_preferred_bot_picks_customer_facing_over_internal():
    captured = [(
        "queryDealershipById", {"id": "2"},
        {"dealership": {"bots": [
            {"id": "527", "config": {"botType": "internal_support"}},
            {"id": "526", "config": {"botType": "seezar"}},
        ]}},
    )]
    d = _dash({"Ejner Hessel": {"id": "2", "bots": ["527", "526"]}}, captured)
    # _latest finds the cached payload, so _wait_for returns before touching the page
    assert d._preferred_bot("2", ["527", "526"]) == "526"


def test_latest_matches_on_variables():
    captured = [
        ("botMetrics", {"botId": "527"}, {"botMetrics": {"tag": "internal"}}),
        ("botMetrics", {"botId": "526"}, {"botMetrics": {"tag": "customer"}}),
    ]
    d = _dash(TABLE, captured)
    assert d._latest("botMetrics", botId="526")["botMetrics"]["tag"] == "customer"
    assert d._latest("botMetrics", botId="527")["botMetrics"]["tag"] == "internal"
    assert d._latest("botMetrics", botId="999") is None


def test_latest_returns_most_recent_for_repeated_operation():
    captured = [
        ("getDealerships", {}, {"dealerships": ["stale"]}),
        ("getDealerships", {}, {"dealerships": ["fresh"]}),
    ]
    assert _dash(TABLE, captured)._latest("getDealerships")["dealerships"] == ["fresh"]


def test_dealerships_skips_entries_without_bots():
    d = Dashboard.__new__(Dashboard)
    d._captured = [("getDealerships", {}, {"dealerships": [
        {"id": "1", "name": "Has Bot", "bots": [{"id": "10"}]},
        {"id": "2", "name": "No Bot", "bots": []},
        {"id": "3", "name": "", "bots": [{"id": "11"}]},
    ]})]
    d._dealer_cache = None
    d._bot_pref = {}
    d.page = None
    assert list(d.dealerships()) == ["Has Bot"]


def test_bot_metrics_reports_the_bot_actually_served():
    """Analytics redirects to a different bot; the caller must be told which.

    Asked for 526, the page fetches 527 - the caller must get 527 back.
    """
    d = _dash({"Ejner Hessel": {"id": "2", "bots": ["526"]}}, [])

    class _Page:
        url = "https://seezar-dashboard.seez.dev/dealership/2/analytics/527"

        def goto(self, *a, **k):
            # The SPA fetches during navigation, which is what the guard requires
            d._captured.append((
                "botMetrics", {"botId": "527"},
                {"botMetrics": {"chatInsights": {"noOfChats": 188}}},
            ))

        def wait_for_timeout(self, *a, **k):
            pass

    d.page = _Page()
    metrics, served = d.bot_metrics("Ejner Hessel")
    assert served == "527", "must report the bot the dashboard actually served"
    assert metrics["chatInsights"]["noOfChats"] == 188


def test_bot_metrics_raises_with_the_page_url_when_nothing_captured(monkeypatch):
    monkeypatch.setattr("seezar_operator.dashboard.CAPTURE_TIMEOUT_MS", 50)
    d = _dash({"Ejner Hessel": {"id": "2", "bots": ["527"]}}, [])

    class _Page:
        url = "https://seezar-dashboard.seez.dev/login"

        def goto(self, *a, **k):
            pass

        def wait_for_timeout(self, *a, **k):
            pass

    d.page = _Page()
    with pytest.raises(RuntimeError, match="login"):
        d.bot_metrics("Ejner Hessel")


def test_bot_metrics_ignores_a_previous_dealerships_payload(monkeypatch):
    """A stale payload must not be served under a new dealership's heading."""
    monkeypatch.setattr("seezar_operator.dashboard.CAPTURE_TIMEOUT_MS", 50)
    stale = ("botMetrics", {"botId": "111"}, {"botMetrics": {"chatInsights": {"noOfChats": 999}}})
    d = _dash({"Other Dealer": {"id": "9", "bots": ["222"]}}, [stale])

    class _Page:
        url = "https://seezar-dashboard.seez.dev/dealership/9/analytics/222"

        def goto(self, *a, **k):
            pass

        def wait_for_timeout(self, *a, **k):
            pass

    d.page = _Page()
    with pytest.raises(RuntimeError, match="No botMetrics"):
        d.bot_metrics("Other Dealer")
