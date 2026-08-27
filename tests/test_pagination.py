import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from seezar_operator.dashboard import Dashboard


class _Match:
    def __init__(self, present, clicks, label):
        self._present, self._clicks, self._label = present, clicks, label

    def count(self):
        return 1 if self._present else 0

    @property
    def first(self):
        return self

    def click(self, **kw):
        if not self._present:
            raise AssertionError("clicked a control that is not present")
        self._clicks.append(self._label)


class _Controls:
    def __init__(self, pages, clicks):
        self._pages, self._clicks = pages, clicks

    def count(self):
        return 1 if self._pages else 0

    @property
    def first(self):
        return self

    def get_by_text(self, text, exact=False):
        return _Match(text in self._pages, self._clicks, text)


class _Page:
    """Minimal stand-in exposing only what the pagination path touches."""

    def __init__(self, pages):
        self.pages = pages
        self.clicks = []

    def locator(self, selector):
        if "paginationControls" in selector:
            return _Controls(self.pages, self.clicks)
        raise AssertionError("unexpected selector %r" % selector)

    def wait_for_timeout(self, *a, **k):
        pass


def _dash(page):
    d = Dashboard.__new__(Dashboard)
    d.page = page
    return d


def test_advances_to_the_next_page():
    page = _Page({"1", "2", "3"})
    assert _dash(page)._next_chat_page(1) is True
    assert page.clicks == ["2"], "must click the page number after the current one"


def test_stops_on_the_last_page():
    """Page 3 of 3: there is no 4 to click."""
    page = _Page({"1", "2", "3"})
    assert _dash(page)._next_chat_page(3) is False
    assert page.clicks == []


def test_stops_when_there_is_no_pagination_control():
    """A dealership whose chats fit on one page has no control at all."""
    assert _dash(_Page(set()))._next_chat_page(1) is False


def test_a_failing_click_ends_paging_rather_than_raising():
    class _Broken(_Page):
        def locator(self, selector):
            controls = super().locator(selector)
            original = controls.get_by_text

            def boom(text, exact=False):
                match = original(text, exact=exact)
                match.click = lambda **kw: (_ for _ in ()).throw(TimeoutError("timeout"))
                return match

            controls.get_by_text = boom
            return controls

    assert _dash(_Broken({"1", "2"}))._next_chat_page(1) is False
