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
    DOWNLOAD_TIMEOUT_MS, DOWNLOAD_ATTEMPTS,
    SEEZAR_EMAIL, SEEZAR_PASSWORD, OTP_SENDER_FILTER, OTP_TIMEOUT_SECONDS,
)

logger = logging.getLogger("seezar.dashboard")

_OP_RE = re.compile(r"(?:query|mutation)\s+(\w+)")


class Dashboard:
    def __init__(self, headless: Optional[bool] = None):
        self.headless = HEADLESS if headless is None else headless
        self._pw = self._browser = self._ctx = self.page = None
        self._captured: List[Tuple[str, dict, dict]] = []
        self._dealer_cache: Optional[Dict[str, dict]] = None
        self._bot_pref: Dict[str, str] = {}

    def __enter__(self) -> "Dashboard":
        self.start()
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def start(self) -> "Dashboard":
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(
            headless=self.headless, slow_mo=0 if self.headless else 120
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

    def _wait_for(self, operation: str, timeout_ms: int = SETTLE_MS, **match_vars) -> Optional[dict]:
        deadline = time.monotonic() + timeout_ms / 1000.0
        while True:
            data = self._latest(operation, **match_vars)
            if data is not None:
                return data
            if time.monotonic() >= deadline:
                return None
            self.page.wait_for_timeout(400)

    def _settle(self, ms: int = SETTLE_MS) -> None:
        self.page.wait_for_timeout(ms)

    def open(self) -> "Dashboard":
        self.page.goto(DASHBOARD_URL, wait_until="domcontentloaded", timeout=NAV_TIMEOUT)
        # The SPA may redirect to /login well after domcontentloaded, so watch for
        # either outcome instead of sleeping a fixed interval and guessing.
        deadline = time.monotonic() + CAPTURE_TIMEOUT_MS / 1000.0
        while time.monotonic() < deadline:
            if "/login" in self.page.url:
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
        self.page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=NAV_TIMEOUT)
        self.page.wait_for_timeout(3000)

        self.page.locator("input[type='email'], input[name='email']").first.fill(SEEZAR_EMAIL)
        if SEEZAR_PASSWORD:
            pw_box = self.page.locator("input[type='password']")
            if pw_box.count():
                pw_box.first.fill(SEEZAR_PASSWORD)
        self.page.locator("button[type='submit']").first.click()
        self.page.wait_for_timeout(6000)

        otp_box = self.page.locator(
            "input[autocomplete='one-time-code'], input[name*='code' i], input[placeholder*='code' i]"
        )
        if otp_box.count():
            code = get_otp_from_gmail(
                sender_filter=OTP_SENDER_FILTER, timeout_seconds=OTP_TIMEOUT_SECONDS
            )
            logger.info("Retrieved OTP from Gmail")
            otp_box.first.fill(code)
            self.page.locator("button[type='submit']").first.click()
            self.page.wait_for_timeout(8000)

        if "/login" in self.page.url:
            raise RuntimeError("Login failed - still on the login page")
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
            self.page.goto(DASHBOARD_URL, wait_until="domcontentloaded", timeout=NAV_TIMEOUT)
            data = self._wait_for("getDealerships", CAPTURE_TIMEOUT_MS)
        if data is None:
            raise RuntimeError(
                "Could not capture the dealership list after %d attempts. "
                "Page URL was %s; GraphQL operations seen: %s"
                % (CAPTURE_ATTEMPTS, self.page.url,
                   sorted({op for op, _, _ in self._captured}) or "none")
            )
        self._dealer_cache = {
            d["name"]: {"id": d["id"], "bots": [b["id"] for b in (d.get("bots") or [])]}
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
            self.page.goto("%s/dealership/%s" % (DASHBOARD_URL, dealer_id),
                           wait_until="domcontentloaded", timeout=NAV_TIMEOUT)
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
        self.page.goto(
            "%s/dealership/%s/chat-history/%s?page=1" % (DASHBOARD_URL, dealer_id, bot_id),
            wait_until="domcontentloaded", timeout=NAV_TIMEOUT,
        )
        listing = self._wait_for("seezarChats", CAPTURE_TIMEOUT_MS, botId=bot_id)
        nodes = ((listing or {}).get("seezarChats") or {}).get("nodes") or []
        logger.info("Conversations tab lists %d chats", len(nodes))
        if len(nodes) > max_chats:
            logger.warning("Reading the first %d of %d chats (--max-chats)", max_chats, len(nodes))

        chats: List[dict] = []
        for node in nodes[:max_chats]:
            ref, user_id = node.get("chatReferenceId"), node.get("userId")
            if not ref or not user_id:
                continue
            try:
                self.page.get_by_text(ref, exact=False).first.click(timeout=15_000)
            except Exception as exc:
                logger.warning("Could not open chat %s: %s", ref, str(exc).splitlines()[0])
                continue
            data = self._wait_for("getUserChatHistory", CAPTURE_TIMEOUT_MS, userId=user_id)
            texts = []
            for item in ((data or {}).get("getUserChatHistory") or {}).get("chatHistory") or []:
                try:
                    payload = json.loads(item.get("contentJson") or "{}")
                except (TypeError, ValueError):
                    continue
                if payload.get("role") == "user" and payload.get("text"):
                    texts.append(str(payload["text"]).strip())
            chats.append({"chat_ref": ref, "user_messages": texts})
            logger.info("  chat %s -> %d customer messages", ref, len(texts))
        return chats, len(nodes)

    def bot_metrics(self, name: str) -> dict:
        dealer_id, bot_id = self.resolve(name)
        logger.info("Opening analytics for %s (dealership=%s bot=%s)", name, dealer_id, bot_id)
        self.page.goto(
            "%s/dealership/%s/analytics/%s" % (DASHBOARD_URL, dealer_id, bot_id),
            wait_until="domcontentloaded", timeout=NAV_TIMEOUT,
        )
        data = self._wait_for("botMetrics", CAPTURE_TIMEOUT_MS, botId=bot_id)
        if data is None or not data.get("botMetrics"):
            raise RuntimeError("No botMetrics response captured for %s" % name)
        return data["botMetrics"]

    def download_chat_history(self, name: str) -> Path:
        dealer_id, _ = self.resolve(name)
        self.page.goto(
            "%s/dealership/%s" % (DASHBOARD_URL, dealer_id),
            wait_until="domcontentloaded", timeout=NAV_TIMEOUT,
        )
        # The header renders late on a slow dashboard, so wait for the control itself
        # rather than a fixed sleep. Prefer the button over its inner span.
        trigger = self.page.locator(
            "button:has-text('Chat History'), .downloadChats"
        ).first
        trigger.wait_for(state="visible", timeout=CAPTURE_TIMEOUT_MS)
        self.page.wait_for_timeout(1500)

        logger.info("Downloading chat history for %s", name)
        dl, last_err = None, None
        for attempt in range(1, DOWNLOAD_ATTEMPTS + 1):
            try:
                with self.page.expect_download(timeout=DOWNLOAD_TIMEOUT_MS) as info:
                    trigger.click()
                dl = info.value
                break
            except Exception as exc:
                last_err = exc
                logger.warning("Chat History download attempt %d/%d failed: %s",
                               attempt, DOWNLOAD_ATTEMPTS, str(exc).splitlines()[0])
        if dl is None:
            raise RuntimeError(
                "Chat History download did not start for %s after %d attempts (%s)"
                % (name, DOWNLOAD_ATTEMPTS, last_err)
            )
        target = DOWNLOADS_DIR / (dl.suggested_filename or "%s-chat-history.zip" % name)
        dl.save_as(target)
        logger.info("Saved %s (%d bytes)", target.name, target.stat().st_size)
        return target
