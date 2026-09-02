from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from playwright.sync_api import sync_playwright

from seezar_operator.config import (
    DASHBOARD_URL, LOGIN_URL, GRAPHQL_HOST, DOWNLOADS_DIR, STORAGE_STATE_PATH,
    HEADLESS, NAV_TIMEOUT, SETTLE_MS, CAPTURE_TIMEOUT_MS, CAPTURE_ATTEMPTS,
    DOWNLOAD_TIMEOUT_MS, DOWNLOAD_ATTEMPTS, DOWNLOAD_DIRECT_WAIT_MS, EXPORT_PROBE_MS,
    SEEZAR_EMAIL, SEEZAR_PASSWORD, OTP_SENDER_FILTER, OTP_TIMEOUT_SECONDS,
)

logger = logging.getLogger("seezar.dashboard")

_OP_RE = re.compile(r"(?:query|mutation)\s+(\w+)")
# An unauthenticated visitor is redirected to /signup, not /login.
_AUTH_RE = re.compile(r"/(?:login|signup)")

# Chromium aborts with these when the machine's own connectivity changes underneath
# a request - WiFi reconnecting, a VPN toggling, a dropped link. They say nothing
# about the dashboard, so they are worth retrying; anything else is a real error.
_TRANSIENT_NET = (
    "ERR_NETWORK_CHANGED", "ERR_CONNECTION_RESET", "ERR_CONNECTION_CLOSED",
    "ERR_CONNECTION_TIMED_OUT", "ERR_CONNECTION_ABORTED", "ERR_INTERNET_DISCONNECTED",
    "ERR_NAME_NOT_RESOLVED", "ERR_PROXY_CONNECTION_FAILED", "ERR_EMPTY_RESPONSE",
    "ERR_SOCKET_NOT_CONNECTED", "ERR_TIMED_OUT", "Timeout",
)
NAV_ATTEMPTS = 3


class Dashboard:
    def __init__(self, headless: Optional[bool] = None):
        self.headless = HEADLESS if headless is None else headless
        self._pw = self._browser = self._ctx = self.page = None
        self._captured: List[Tuple[str, dict, dict]] = []
        self._dealer_cache: Optional[Dict[str, dict]] = None
        self._bot_pref: Dict[str, str] = {}
        self._metrics_fp: Dict[str, str] = {}   # payload fingerprint -> bot that served it

    def __enter__(self) -> "Dashboard":
        self.start()
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def start(self) -> "Dashboard":
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(
            headless=self.headless,
            slow_mo=0 if self.headless else 120,
            # Chromium's default /dev/shm is small; a tab left idle for the minutes
            # an export can take will otherwise sometimes crash outright.
            args=["--disable-dev-shm-usage"],
        )
        ctx_kwargs: Dict[str, Any] = {
            "viewport": {"width": 1600, "height": 1000},
            "accept_downloads": True,
        }
        if STORAGE_STATE_PATH.exists():
            ctx_kwargs["storage_state"] = str(STORAGE_STATE_PATH)
        self._ctx = self._browser.new_context(**ctx_kwargs)
        self.page = self._ctx.new_page()
        self.page.on("response", self._on_response)
        return self

    def close(self) -> None:
        for obj, meth in ((self._browser, "close"), (self._pw, "stop")):
            try:
                if obj:
                    getattr(obj, meth)()
            except Exception as exc:
                logger.debug("Error during teardown: %s", exc)

    def _on_response(self, resp) -> None:
        if GRAPHQL_HOST not in resp.url:
            return
        try:
            payload = json.loads(resp.request.post_data or "")
            match = _OP_RE.search(payload.get("query", ""))
            body = json.loads(resp.text())
            if match and "data" in body:
                self._captured.append((match.group(1), payload.get("variables") or {}, body["data"]))
        except Exception as exc:
            logger.debug("Skipped a GraphQL response from %s: %s", resp.url, exc)

    def _latest(self, operation: str, **match_vars) -> Optional[dict]:
        for op, variables, data in reversed(self._captured):
            if op != operation:
                continue
            if all(str(variables.get(k)) == str(v) for k, v in match_vars.items()):
                return data
        return None

    def _goto(self, url: str) -> None:
        """Navigate, retrying transient network failures.

        A laptop reconnecting to WiFi, a VPN toggling, or a dropped connection makes
        Chromium abort with net::ERR_NETWORK_CHANGED and similar. These are local and
        momentary, and losing a run that may already be minutes in - or a live demo -
        to one of them is not acceptable.
        """
        last_err = None
        for attempt in range(1, NAV_ATTEMPTS + 1):
            try:
                self.page.goto(url, wait_until="domcontentloaded", timeout=NAV_TIMEOUT)
                return
            except Exception as exc:
                message = str(exc)
                if not any(marker in message for marker in _TRANSIENT_NET):
                    raise                       # a real error: fail loudly, do not mask it
                last_err = exc
                logger.warning("Navigation to %s failed (%d/%d): %s",
                               url, attempt, NAV_ATTEMPTS, message.splitlines()[0][:110])
                if attempt < NAV_ATTEMPTS:
                    self.page.wait_for_timeout(3000 * attempt)
        raise RuntimeError(
            "Could not load %s after %d attempts - the network kept dropping. "
            "Check the connection and run again. Last error: %s"
            % (url, NAV_ATTEMPTS, str(last_err).splitlines()[0][:160])
        )

    def _wait_for(self, operation: str, timeout_ms: int = SETTLE_MS, **match_vars) -> Optional[dict]:
        deadline = time.monotonic() + timeout_ms / 1000.0
        while True:
            data = self._latest(operation, **match_vars)
            if data is not None:
                return data
            if time.monotonic() >= deadline:
                return None
            self.page.wait_for_timeout(400)

    def open(self) -> "Dashboard":
        self._goto(DASHBOARD_URL)
        # The SPA may redirect to /login well after domcontentloaded, so watch for
        # either outcome instead of sleeping a fixed interval and guessing.
        deadline = time.monotonic() + CAPTURE_TIMEOUT_MS / 1000.0
        while time.monotonic() < deadline:
            if _AUTH_RE.search(self.page.url):
                self.login()
                break
            if self._latest("getDealerships") is not None:
                return self
            self.page.wait_for_timeout(400)
        self._wait_for("getDealerships", CAPTURE_TIMEOUT_MS)
        return self

    def login(self) -> None:
        from seezar_operator.utils.otp_fetcher import get_otp_from_gmail

        if not SEEZAR_EMAIL:
            raise RuntimeError("SEEZAR_EMAIL is not set in .env")
        logger.info("Session expired - performing login for %s", SEEZAR_EMAIL)
        self._goto(LOGIN_URL)

        email_box = self.page.locator("input[type='email'], #email, input[name='email']").first
        email_box.wait_for(state="visible", timeout=CAPTURE_TIMEOUT_MS)

        # The page opens in sign-up mode, where the primary button reads
        # "Create account". Switch to log-in before touching anything else.
        toggle = self.page.get_by_text("Already have an account", exact=False)
        if toggle.count():
            toggle.first.click(timeout=15_000)
            self.page.wait_for_timeout(2500)
            logger.info("Switched the form from sign-up to log-in")

        email_box.fill(SEEZAR_EMAIL)
        if SEEZAR_PASSWORD:
            pw_box = self.page.locator("input[type='password']")
            if pw_box.count():
                pw_box.first.fill(SEEZAR_PASSWORD)

        # Filling the email enables the primary button; clicking it while still
        # disabled silently does nothing.
        submit = self.page.locator(
            "button.primary, button[class*='primary'], button[type='submit']"
        ).first
        submit.wait_for(state="visible", timeout=30_000)
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            try:
                if submit.is_enabled():
                    break
            except Exception:  # element re-rendering between checks
                pass
            self.page.wait_for_timeout(500)
        submit.click(timeout=15_000)

        # The one-time-code field can take longer than a fixed sleep to render.
        # Polling for it - or for the page leaving the auth screen - avoids
        # silently skipping the whole OTP step and then failing on the URL check.
        otp_box = self.page.locator(
            "input[autocomplete='one-time-code'], input[name*='code' i], input[placeholder*='code' i]"
        )
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if otp_box.count() or not _AUTH_RE.search(self.page.url):
                break
            self.page.wait_for_timeout(500)

        if otp_box.count():
            code = get_otp_from_gmail(
                sender_filter=OTP_SENDER_FILTER, timeout_seconds=OTP_TIMEOUT_SECONDS
            )
            logger.info("Retrieved OTP from Gmail")
            otp_box.first.fill(code)
            self.page.locator("button[type='submit']").first.click()
            self.page.wait_for_timeout(8000)

        if _AUTH_RE.search(self.page.url):
            raise RuntimeError("Login failed - still on %s" % self.page.url)
        self._ctx.storage_state(path=str(STORAGE_STATE_PATH))
        logger.info("Login succeeded; session persisted")

    def dealerships(self) -> Dict[str, dict]:
        if self._dealer_cache is not None:
            return self._dealer_cache
        data = self._wait_for("getDealerships", CAPTURE_TIMEOUT_MS)
        for attempt in range(2, CAPTURE_ATTEMPTS + 1):
            if data is not None:
                break
            logger.warning("Dealership list not captured; reloading (attempt %d/%d)",
                           attempt, CAPTURE_ATTEMPTS)
            self._goto(DASHBOARD_URL)
            data = self._wait_for("getDealerships", CAPTURE_TIMEOUT_MS)
        if data is None:
            raise RuntimeError(
                "Could not capture the dealership list after %d attempts. "
                "Page URL was %s; GraphQL operations seen: %s"
                % (CAPTURE_ATTEMPTS, self.page.url,
                   sorted({op for op, _, _ in self._captured}) or "none")
            )
        # parent/unit are carried so a franchise can explain, rather than just time
        # out, when the dashboard offers no Chat History export on its own page.
        self._dealer_cache = {
            d["name"]: {
                "id": d["id"],
                "bots": [b["id"] for b in (d.get("bots") or [])],
                "parent": d.get("parentId"),
                "unit": d.get("businessUnitType"),
            }
            for d in data["dealerships"]
            if d.get("name") and d.get("bots")
        }
        logger.info("Discovered %d dealerships with bots", len(self._dealer_cache))
        return self._dealer_cache

    def resolve(self, name: str) -> Tuple[str, str]:
        table = self.dealerships()
        if name in table:
            entry = table[name]
        else:
            hits = [k for k in table if name.lower() in k.lower()]
            if not hits:
                raise LookupError("No dealership with a bot matches %r" % name)
            if len(hits) > 1 and name.lower() not in (h.lower() for h in hits):
                raise LookupError("%r is ambiguous: %s" % (name, hits[:5]))
            entry = table[sorted(hits, key=len)[0]]
        return entry["id"], self._preferred_bot(entry["id"], entry["bots"])

    def _preferred_bot(self, dealer_id: str, bots: List[str]) -> str:
        """A dealership can host several bots. Ejner Hessel's first bot is an
        internal HR assistant; the customer-facing one is botType 'seezar'."""
        if len(bots) == 1:
            return bots[0]
        if dealer_id in self._bot_pref:
            return self._bot_pref[dealer_id]

        # Often already captured by an earlier navigation - only pay for a page
        # load when it genuinely has not been seen yet.
        data = self._latest("queryDealershipById", id=dealer_id)
        if data is None:
            self._goto("%s/dealership/%s" % (DASHBOARD_URL, dealer_id))
            data = self._wait_for("queryDealershipById", CAPTURE_TIMEOUT_MS, id=dealer_id)
        chosen = bots[0]
        for bot in ((data or {}).get("dealership") or {}).get("bots") or []:
            if ((bot.get("config") or {}).get("botType") or "").lower() == "seezar":
                chosen = bot["id"]
                break
        if chosen != bots[0]:
            logger.info("Dealership %s has %d bots; using customer-facing bot %s",
                        dealer_id, len(bots), chosen)
        self._bot_pref[dealer_id] = chosen
        return chosen

    def conversations(self, name: str, max_chats: int = 25) -> Tuple[List[dict], int]:
        """Open the Conversations tab and read each chat, as an operator would."""
        dealer_id, bot_id = self.resolve(name)
        logger.info("Opening Conversations tab for %s (bot=%s)", name, bot_id)
        self._goto("%s/dealership/%s/chat-history/%s?page=1" % (DASHBOARD_URL, dealer_id, bot_id))
        listing = self._wait_for("seezarChats", CAPTURE_TIMEOUT_MS, botId=bot_id)
        nodes = ((listing or {}).get("seezarChats") or {}).get("nodes") or []
        logger.info("Conversations tab lists %d chats", len(nodes))
        if len(nodes) > max_chats:
            logger.warning("Reading the first %d of %d chats (--max-chats)", max_chats, len(nodes))

        by_ref = {n["chatReferenceId"]: n["userId"] for n in nodes
                  if n.get("chatReferenceId") and n.get("userId")}
        chats: List[dict] = []
        seen: set = set()
        page = 1

        # The list renders 20 rows per page while the query returns every chat,
        # so rows beyond the current page are absent from the DOM until paged to.
        while len(chats) < max_chats:
            rendered = self.page.inner_text("body")
            on_page = [r for r in by_ref if r not in seen and r in rendered]
            for ref in on_page:
                if len(chats) >= max_chats:
                    break
                seen.add(ref)
                chats.append(self._read_chat(ref, by_ref[ref]))
            if len(chats) >= max_chats or not self._next_chat_page(page):
                break
            page += 1

        return [c for c in chats if c], len(nodes)

    def _read_chat(self, ref: str, user_id: str) -> Optional[dict]:
        try:
            self.page.get_by_text(ref, exact=False).first.click(timeout=15_000)
        except Exception as exc:
            logger.warning("Could not open chat %s: %s", ref, str(exc).splitlines()[0])
            return None
        data = self._wait_for("getUserChatHistory", CAPTURE_TIMEOUT_MS, userId=user_id)
        texts = []
        for item in ((data or {}).get("getUserChatHistory") or {}).get("chatHistory") or []:
            try:
                payload = json.loads(item.get("contentJson") or "{}")
            except (TypeError, ValueError):
                continue
            if payload.get("role") == "user" and payload.get("text"):
                texts.append(str(payload["text"]).strip())
        logger.info("  chat %s -> %d customer messages", ref, len(texts))
        return {"chat_ref": ref, "user_messages": texts}

    def _next_chat_page(self, current: int) -> bool:
        controls = self.page.locator(".paginationControls").first
        if not controls.count():
            return False
        target = controls.get_by_text(str(current + 1), exact=True)
        if not target.count():
            return False
        try:
            target.first.click(timeout=15_000)
        except Exception as exc:
            logger.warning("Could not open chat page %d: %s", current + 1, str(exc).splitlines()[0])
            return False
        self.page.wait_for_timeout(6000)
        logger.info("Advanced to chat list page %d", current + 1)
        return True

    def bot_metrics(self, name: str) -> Tuple[dict, str]:
        """Analytics payload plus the bot it actually describes.

        The dashboard serves Analytics for one bot only and redirects to it, so
        the bot asked for is not always the bot reported on. Whichever botMetrics
        the page fetches is the truth; the caller is told which one it was."""
        dealer_id, bot_id = self.resolve(name)
        logger.info("Opening analytics for %s (dealership=%s bot=%s)", name, dealer_id, bot_id)

        # Only accept a payload fetched after this navigation. Matching on any
        # botMetrics would hand back a previous dealership's figures if this
        # page failed to load - wrong numbers under a correct-looking heading.
        seen_before = len(self._captured)
        self._goto("%s/dealership/%s/analytics/%s" % (DASHBOARD_URL, dealer_id, bot_id))

        deadline = time.monotonic() + CAPTURE_TIMEOUT_MS / 1000.0
        while True:
            for op, variables, data in reversed(self._captured[seen_before:]):
                if op == "botMetrics" and data.get("botMetrics"):
                    served = str(variables.get("botId") or bot_id)
                    if served != bot_id:
                        logger.info("Analytics is served for bot %s, not %s", served, bot_id)
                    return data["botMetrics"], served
            if time.monotonic() >= deadline:
                raise RuntimeError(
                    "No botMetrics response captured for %s. Page URL was %s"
                    % (name, self.page.url)
                )
            self.page.wait_for_timeout(400)

    def duplicate_metrics_bot(self, payload: dict, served_bot: str) -> Optional[str]:
        """If an earlier bot in this session returned a byte-identical botMetrics
        payload, return that bot's id. The analytics endpoint serves the same mock
        data for every bot, so this lets a report prove it rather than assert it."""
        fingerprint = json.dumps(payload, sort_keys=True)
        previous = self._metrics_fp.get(fingerprint)
        self._metrics_fp.setdefault(fingerprint, served_bot)
        return previous if previous and previous != served_bot else None

    def _wait_until_enabled(self, trigger, name: str,
                            budget_s: Optional[float] = None) -> None:
        """Wait for the export button to come back.

        The dashboard disables it while it builds an archive and re-enables it when
        the archive is ready, which makes that transition the only progress signal
        the export has. Clicking while it is disabled just burns Playwright's
        actionability timeout and reports "element is not enabled"; clicking once it
        is enabled again serves the built file in seconds."""
        if trigger.is_enabled():
            return
        budget = DOWNLOAD_TIMEOUT_MS / 1000.0 if budget_s is None else max(budget_s, 0.0)
        logger.info("Export button is disabled - an archive for %s is already being "
                    "generated; waiting up to %ds", name, int(budget))
        started, announced = time.monotonic(), 0.0
        while time.monotonic() - started < budget:
            if trigger.is_enabled():
                logger.info("Export button enabled again after %ds",
                            int(time.monotonic() - started))
                return
            waited = time.monotonic() - started
            if waited - announced >= 20:
                announced = waited
                logger.info("  still generating (%ds elapsed)", int(waited))
            self.page.wait_for_timeout(2000)
        raise RuntimeError(
            "The Chat History button for %s was still disabled after %ds. The "
            "dashboard is generating an archive and has not finished - wait a few "
            "minutes and run again." % (name, int(budget))
        )

    def _no_export_reason(self, name: str) -> str:
        """Explain a missing Chat History button. The dashboard only offers the
        export on a parent business unit; a franchise's conversations are inside
        the parent's archive, which is why a group export holds several CSVs."""
        table = self.dealerships()
        entry = table.get(name) or {}
        parent_id = entry.get("parent")
        unit = entry.get("unit") or "business unit"
        if parent_id:
            parent = next((n for n, v in table.items() if v["id"] == parent_id), None)
            if parent:
                return (
                    "%s is a %s under %r, and the dashboard offers the Chat History "
                    "export only on the parent. The parent's archive already contains "
                    "this unit's CSV - run Scenario II on %r instead."
                    % (name, unit, parent, parent)
                )
            return ("%s is a %s whose parent (id %s) is not in the dealership list, "
                    "and only a parent offers the Chat History export."
                    % (name, unit, parent_id))
        return ("No Chat History export button is present for %s (%s). The dashboard "
                "does not offer the export for this business unit." % (name, unit))

    def download_chat_history(self, name: str) -> Path:
        dealer_id, _ = self.resolve(name)
        self._goto("%s/dealership/%s" % (DASHBOARD_URL, dealer_id))
        # The dashboard's own test id, which survives copy changes and does not
        # collide with the "Chat History" panel heading on the Conversations tab.
        trigger = self.page.locator(
            '[data-test-id="downloand-csv-button"], button.downloadChatsButton, '
            "button:has-text('Chat History')"
        ).first
        # The button renders within a couple of seconds when it exists at all, so a
        # short probe is enough; a longer one only delays a clear explanation.
        try:
            trigger.wait_for(state="visible", timeout=EXPORT_PROBE_MS)
        except Exception:
            raise RuntimeError(self._no_export_reason(name)) from None

        logger.info("Downloading chat history for %s - the server builds the archive "
                    "on demand; the export button going disabled and back is how it "
                    "reports progress, so that is what is watched, for up to %ds",
                    name, DOWNLOAD_TIMEOUT_MS // 1000)
        dl, last_err = None, None
        deadline = time.monotonic() + DOWNLOAD_TIMEOUT_MS / 1000.0
        for attempt in range(1, DOWNLOAD_ATTEMPTS + 1):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                logger.warning("Out of time budget after %d attempt(s)", attempt - 1)
                break
            window = max(min(DOWNLOAD_DIRECT_WAIT_MS, int(remaining * 1000)), 1000)
            try:
                # Never click a disabled button - that is the dashboard saying a build
                # is running, and waiting for it to re-enable is waiting for the
                # archive. This is also where a build started by a previous click, or
                # by an earlier run, is sat out.
                self._wait_until_enabled(trigger, name, budget_s=remaining)
                if attempt > 1:
                    logger.info("Export button is enabled again - clicking to fetch "
                                "the archive (attempt %d/%d)", attempt, DOWNLOAD_ATTEMPTS)
                # Short on purpose. A warm archive is served in 2-6s; anything longer
                # means this click started a build rather than served one, and the
                # useful thing to do then is watch the button, not keep waiting here.
                with self.page.expect_download(timeout=window) as info:
                    trigger.click(timeout=60_000)
                dl = info.value
                break
            except Exception as exc:
                last_err = exc
                logger.info("No file %ds after click %d/%d - treating it as the click "
                            "that started the build, and waiting for the button",
                            window // 1000, attempt, DOWNLOAD_ATTEMPTS)
                logger.debug("Download attempt %d failed: %s", attempt,
                             str(exc).splitlines()[0])
                self.page.wait_for_timeout(2000)
        if dl is None:
            raise RuntimeError(
                "Chat History download did not start for %s after %d attempts (%s)"
                % (name, DOWNLOAD_ATTEMPTS, last_err)
            )
        target = DOWNLOADS_DIR / (dl.suggested_filename or "%s-chat-history.zip" % name)
        dl.save_as(target)
        logger.info("Saved %s (%d bytes)", target.name, target.stat().st_size)
        return target
