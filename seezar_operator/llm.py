from __future__ import annotations

import json
import logging
import re
import time
from typing import Dict, List

import requests
from concurrent.futures import ThreadPoolExecutor, as_completed

from seezar_operator.config import (
    OPENROUTER_API_KEY, OPENROUTER_MODEL, OPENROUTER_URL, TOPICS,
)

logger = logging.getLogger("seezar.llm")

BATCH_SIZE = 25
MAX_RETRIES = 3
MAX_TOKENS = 3000
MAX_WORKERS = 3

SYSTEM_PROMPT = (
    "You analyse customer messages sent to car-dealership chatbots. "
    "Messages may be in Danish, English or German. "
    "For each numbered message return: the single best topic from this list - "
    + ", ".join(TOPICS)
    + " - and any vehicle models explicitly mentioned (make and/or model, e.g. "
    '"Toyota Camry", "Porsche"). Use an empty list when no model is named. '
    'Reply with ONLY a compact JSON array of objects: '
    '[{"i": <index>, "topic": "<topic>", "models": ["<model>"]}]. No prose, no markdown.'
)


class LLMUnavailable(RuntimeError):
    pass


class _Transient(Exception):
    pass


def _post(messages: List[dict]) -> str:
    if not OPENROUTER_API_KEY:
        raise LLMUnavailable("OPENROUTER_API_KEY is not set in .env")
    last_err = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.post(
                OPENROUTER_URL,
                headers={
                    "Authorization": "Bearer %s" % OPENROUTER_API_KEY,
                    "Content-Type": "application/json",
                },
                json={
                    "model": OPENROUTER_MODEL,
                    "messages": messages,
                    "temperature": 0,
                    "max_tokens": MAX_TOKENS,
                },
                timeout=90,
            )
            if resp.status_code in (401, 402, 403):
                raise LLMUnavailable(
                    "OpenRouter rejected the key (HTTP %s): %s" % (resp.status_code, resp.text[:200])
                )
            if resp.status_code != 200:
                raise _Transient("HTTP %s: %s" % (resp.status_code, resp.text[:200]))
            body = resp.json()
            if "error" in body:
                raise _Transient("payload error: %s" % json.dumps(body["error"])[:200])
            content = (body.get("choices") or [{}])[0].get("message", {}).get("content")
            if not content or not content.strip():
                raise _Transient("empty content in response")
            return content
        except LLMUnavailable:
            raise
        except Exception as exc:
            last_err = exc
            logger.warning("OpenRouter attempt %d/%d failed: %s", attempt, MAX_RETRIES, exc)
            time.sleep(2 * attempt)
    raise LLMUnavailable("OpenRouter unreachable after %d attempts: %s" % (MAX_RETRIES, last_err))


def _parse_array(content: str) -> List[dict]:
    content = re.sub(r"^```(?:json)?|```$", "", content.strip(), flags=re.MULTILINE).strip()
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        pass

    match = re.search(r"\[.*\]", content, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass

    # Truncated response: keep whatever complete objects arrived instead of losing the batch.
    salvaged = []
    for chunk in re.findall(r"\{[^{}]*\}", content):
        try:
            salvaged.append(json.loads(chunk))
        except json.JSONDecodeError:
            continue
    if salvaged:
        logger.warning("Salvaged %d objects from a malformed/truncated response", len(salvaged))
        return salvaged
    raise LLMUnavailable("Could not parse JSON from model output: %s" % content[:200])


def analyse(messages: List[str]) -> List[Dict]:
    results: List[Dict] = [
        {"message": m, "topic": None, "models": []} for m in messages
    ]
    valid = set(TOPICS)
    starts = list(range(0, len(messages), BATCH_SIZE))

    def _ask(chunk: List[str]) -> List[dict]:
        numbered = "\n".join(
            "%d: %s" % (i, m.replace("\n", " ")[:400]) for i, m in enumerate(chunk)
        )
        return _parse_array(_post([
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": numbered},
        ]))

    def classify(start: int):
        chunk = messages[start:start + BATCH_SIZE]
        items = _ask(chunk)
        covered = {int(i["i"]) for i in items
                   if str(i.get("i", "")).lstrip("-").isdigit() and 0 <= int(i["i"]) < len(chunk)}

        # A short reply would otherwise leave those messages on their "other"
        # default, which reads as a classification rather than a missing answer.
        missing = [i for i in range(len(chunk)) if i not in covered]
        if missing:
            logger.warning("Batch at %d returned %d/%d labels; retrying %d message(s)",
                           start, len(covered), len(chunk), len(missing))
            retry = _ask([chunk[i] for i in missing])
            for item in retry:
                try:
                    local = int(item["i"])
                except (KeyError, TypeError, ValueError):
                    continue
                if 0 <= local < len(missing):
                    item["i"] = missing[local]
                    items.append(item)

        return start, len(chunk), items

    logger.info("Classifying %d messages in %d batches (%d workers)",
                len(messages), len(starts), MAX_WORKERS)
    done = 0
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        for future in as_completed([pool.submit(classify, s) for s in starts]):
            start, size, items = future.result()
            for item in items:
                try:
                    idx = int(item["i"])
                except (KeyError, TypeError, ValueError):
                    continue
                if not 0 <= idx < size:
                    continue
                topic = str(item.get("topic", "other")).strip().lower()
                models = [str(m).strip() for m in (item.get("models") or []) if str(m).strip()]
                # Index is batch-relative; offset it back onto the full message list.
                results[start + idx].update(
                    topic=topic if topic in valid else "other",
                    models=models,
                )
            done += 1
            logger.info("  batch %d/%d complete", done, len(starts))

    unlabelled = sum(1 for r in results if r["topic"] is None)
    if unlabelled:
        logger.warning("%d of %d messages could not be classified and are counted "
                       "as 'other'", unlabelled, len(messages))
    for r in results:
        if r["topic"] is None:
            r["topic"] = "other"
    return results
