import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from seezar_operator import dashboard
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


class _FlakyPage:
    """A page whose goto() fails a set number of times before succeeding."""

    def __init__(self, failures, message="net::ERR_NETWORK_CHANGED at https://x/"):
        self.failures = failures
        self.message = message
        self.attempts = 0
        self.slept = 0

    def goto(self, url, **kwargs):
        self.attempts += 1
        if self.attempts <= self.failures:
            raise RuntimeError(self.message)

    def wait_for_timeout(self, ms):
        self.slept += ms


def _dash_with_page(page):
    d = Dashboard.__new__(Dashboard)
    d.page = page
    return d


def test_navigation_retries_a_transient_network_error():
    page = _FlakyPage(failures=2)
    _dash_with_page(page)._goto("https://example.test/")
    assert page.attempts == 3, "should retry until it succeeds"


def test_navigation_gives_up_with_a_clear_message():
    page = _FlakyPage(failures=99)
    with pytest.raises(RuntimeError, match="network kept dropping"):
        _dash_with_page(page)._goto("https://example.test/")
    assert page.attempts == 3, "bounded by NAV_ATTEMPTS"


def test_a_real_error_is_not_retried():
    """A 404 or a bad selector must surface immediately, not be masked as flakiness."""
    page = _FlakyPage(failures=99, message="net::ERR_ABORTED - page not found")
    with pytest.raises(RuntimeError, match="ERR_ABORTED"):
        _dash_with_page(page)._goto("https://example.test/")
    assert page.attempts == 1, "non-transient errors must fail on the first attempt"


class _ExportButton:
    """The export button as the dashboard drives it: enabled to start with, disabled
    while a build runs after the click that starts one, then enabled again with the
    archive ready and served by the next click."""

    def __init__(self, clicks_that_build=1, finish_after_polls=3):
        self.clicks = 0
        self.clicks_that_build = clicks_that_build
        self.finish_after_polls = finish_after_polls
        self.building = False
        self.polls = 0

    def is_enabled(self):
        if self.building:
            self.polls += 1
            if self.polls >= self.finish_after_polls:
                self.building = False
        return not self.building

    def click(self, **kwargs):
        self.clicks += 1
        if self.clicks <= self.clicks_that_build:
            self.building, self.polls = True, 0

    def wait_for(self, **kwargs):
        pass


class _Downloaded:
    suggested_filename = "chat-history.zip"

    def save_as(self, path):
        Path(path).write_text("archive")


class _ExportPage:
    """A page whose download only arrives from a click made while no build is running."""

    def __init__(self, button):
        self.button = button
        self.windows = []
        self.slept = 0

    def goto(self, url, **kwargs):
        pass

    def locator(self, selector):
        return self

    @property
    def first(self):
        return self.button

    def wait_for_timeout(self, ms):
        self.slept += ms

    def expect_download(self, timeout):
        self.windows.append(timeout)
        page = self

        class _Expectation:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                if page.button.building:
                    raise TimeoutError(
                        "Timeout %dms exceeded while waiting for event \"download\"" % timeout
                    )
                return False

            @property
            def value(self):
                return _Downloaded()

        return _Expectation()


def _export_dash(page):
    d = Dashboard.__new__(Dashboard)
    d.page = page
    d._dealer_cache = {"Croxdale": {"id": "243", "bots": ["243"], "parent": None,
                                    "unit": "dealership"}}
    d._bot_pref = {}
    return d


def test_the_click_that_starts_a_build_is_not_waited_out_blindly(tmp_path, monkeypatch):
    """The regression this exists for: a Croxdale run clicked, sat through a 420s
    blind download timeout, then got the file 2s after the retry click. The long wait
    belongs on the button - which the dashboard disables while it builds - not on a
    download timeout that reports nothing while it runs."""
    monkeypatch.setattr(dashboard, "DOWNLOADS_DIR", tmp_path)
    button = _ExportButton(clicks_that_build=1)
    page = _ExportPage(button)

    saved = _export_dash(page).download_chat_history("Croxdale")

    assert button.clicks == 2, "one click to build, one to fetch the built archive"
    assert page.windows[0] == dashboard.DOWNLOAD_DIRECT_WAIT_MS, (
        "the post-click wait must be the short one, not the whole budget"
    )
    assert page.windows[0] < dashboard.DOWNLOAD_TIMEOUT_MS
    assert saved.read_text() == "archive"


def test_an_archive_served_straight_away_costs_one_click(tmp_path, monkeypatch):
    """A warm archive is served in 2-6s. That path must not be slowed down by the
    retry logic added for the cold one."""
    monkeypatch.setattr(dashboard, "DOWNLOADS_DIR", tmp_path)
    button = _ExportButton(clicks_that_build=0)
    page = _ExportPage(button)

    _export_dash(page).download_chat_history("Croxdale")

    assert button.clicks == 1
    assert len(page.windows) == 1


def test_wait_until_enabled_returns_as_soon_as_the_build_finishes():
    button = _ExportButton(clicks_that_build=1)
    button.click()
    assert button.building is True
    _export_dash(_ExportPage(button))._wait_until_enabled(button, "Croxdale", budget_s=30)
    assert button.building is False, "returns only once the button reports it is done"


def test_wait_until_enabled_respects_a_caller_budget():
    """The loop hands it whatever is left of the overall budget, so an exhausted run
    must not be held for the full default."""
    button = _ExportButton(clicks_that_build=1, finish_after_polls=10**9)
    button.click()
    dash = _export_dash(_ExportPage(button))
    with pytest.raises(RuntimeError, match="still disabled"):
        dash._wait_until_enabled(button, "Croxdale", budget_s=0.05)
