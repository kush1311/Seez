from __future__ import annotations

import logging
import re
from collections import Counter
from typing import List, Optional

from seezar_operator import llm, report
from seezar_operator.dashboard import Dashboard

logger = logging.getLogger("seezar.scenario3")

MAX_MESSAGES = 200
MAX_CHATS = 25


def _vehicle_insights(metrics: dict) -> tuple:
    q = (metrics.get("queryInsights") or {}).get("vehicleQueryInsights") or {}
    rows = [(v.get("name"), int(v.get("total") or 0)) for v in (q.get("vehicleInsights") or [])]
    rows.sort(key=lambda r: r[1], reverse=True)
    return q.get("mostQueried"), rows


def _top_model(most_queried, analytics_models) -> Optional[str]:
    if analytics_models and analytics_models[0][0]:
        return analytics_models[0][0]
    return most_queried


def _mentions(model: str, texts: List[str]) -> int:
    """Count texts naming the model - the full name, or the model word alone so
    "Camry" counts for "Toyota Camry". The make alone ("Toyota") does not: that
    is interest in the brand, not in this model."""
    if not model:
        return 0
    tokens = [t for t in re.split(r"\s+", model.strip()) if t]
    patterns = [re.escape(model)]
    if len(tokens) > 1:
        patterns += [re.escape(t) for t in tokens[1:] if len(t) > 2]
    rx = re.compile(r"\b(?:%s)\b" % "|".join(patterns), re.IGNORECASE)
    return sum(1 for t in texts if rx.search(t))


def run(dealership: str, dash: Optional[Dashboard] = None,
        max_messages: int = MAX_MESSAGES, max_chats: int = MAX_CHATS) -> dict:
    own = dash is None
    dash = dash or Dashboard().start().open()
    try:
        metrics, analytics_bot = dash.bot_metrics(dealership)
        chats, chats_listed = dash.conversations(dealership, max_chats=max_chats)
    finally:
        if own:
            dash.close()

    most_queried, analytics_models = _vehicle_insights(metrics)
    top_model = _top_model(most_queried, analytics_models)

    chats_with_model = [c for c in chats if _mentions(top_model, c["user_messages"]) > 0]

    messages = [m for c in chats for m in c["user_messages"]]
    sampled = messages[:max_messages]
    logger.info("Analysing %d customer messages from %d chats", len(sampled), len(chats))
    if not sampled:
        raise RuntimeError("No customer messages found in the Conversations tab for %s" % dealership)

    records = llm.analyse(sampled)

    topic_counts = Counter(r["topic"] for r in records)
    model_counts: Counter = Counter()
    for r in records:
        for m in r["models"]:
            model_counts[m.title()] += 1

    # "What topics people discuss around them" - restricted to messages that
    # actually name a vehicle, which is a different distribution to all traffic.
    around = [r for r in records if r["models"]]
    around_topics = Counter(r["topic"] for r in around)

    mentioned = {m.lower() for m in model_counts}
    analytics_named = {n.lower() for n, _ in analytics_models if n}
    if most_queried:
        analytics_named.add(most_queried.lower())
    overlap = sorted(analytics_named & mentioned)

    discrepancy = None
    if analytics_models and most_queried:
        top_by_count = analytics_models[0][0]
        if top_by_count and top_by_count.lower() != most_queried.lower():
            discrepancy = (
                "Analytics reports mostQueried=%r, but its own vehicleInsights ranks "
                "%r highest (%d clicks)." % (most_queried, top_by_count, analytics_models[0][1])
            )

    result = {
        "dealership": dealership,
        "analytics_bot": analytics_bot,
        "top_model": top_model,
        "analytics_most_queried": most_queried,
        "analytics_models": analytics_models,
        "chats_listed": chats_listed,
        "chats_read": len(chats),
        "chats_mentioning_top_model": len(chats_with_model),
        "messages_total": len(messages),
        "messages_analysed": len(sampled),
        "topic_counts": dict(topic_counts.most_common()),
        "around_topic_counts": dict(around_topics.most_common()),
        "model_counts": dict(model_counts.most_common(15)),
        "overlap": overlap,
        "discrepancy": discrepancy,
    }

    report.header("Scenario III - Deep-Dive Explorer: %s" % dealership)
    report.table("Analytics - models most clicked", ["Model", "Clicks"],
                 analytics_models or [["(none reported)", "-"]])
    if discrepancy:
        report.note(discrepancy)

    report.table(
        "Conversations tab - users mentioning %r" % top_model,
        ["Measure", "Value"],
        [
            ["Chats listed in Conversations tab", chats_listed],
            ["Chats opened and read", len(chats)],
            ["Customer messages in those chats", len(messages)],
            ["Chats mentioning this model", len(chats_with_model)],
        ],
    )
    report.table("Models customers mentioned (%d messages)" % len(sampled),
                 ["Model", "Mentions"],
                 list(model_counts.most_common(10)) or [["(none found)", "-"]])
    report.table("What customers discuss - all messages", ["Topic", "Messages", "Share"],
                 [[t, c, "%.1f%%" % (100.0 * c / len(sampled))]
                  for t, c in topic_counts.most_common()])
    if around:
        report.table("What customers discuss - only messages naming a vehicle (%d)" % len(around),
                     ["Topic", "Messages", "Share"],
                     [[t, c, "%.1f%%" % (100.0 * c / len(around))]
                      for t, c in around_topics.most_common()])
    if not overlap:
        report.note(
            "No model named in Analytics appears in any customer message - the "
            "analytics widget and the conversation data do not corroborate each other."
        )

    md = ["# Scenario III - Deep-Dive Explorer", "",
          "**Dealership:** %s  " % dealership,
          "**Most-clicked model (Analytics):** %s  " % top_model,
          "**Analytics served for bot:** `%s`  " % analytics_bot,
          "**Source:** Conversations tab, %d of %d chats read  " % (len(chats), chats_listed),
          "**Topic model:** `%s` via OpenRouter" % llm.OPENROUTER_MODEL, "",
          "## How many users mentioned this model", "",
          report.md_table(
              ["Measure", "Value"],
              [["Chats listed in the Conversations tab", chats_listed],
               ["Chats opened and read", len(chats)],
               ["Customer messages in those chats", len(messages)],
               ["Chats mentioning `%s`" % top_model, len(chats_with_model)]],
          ), ""]
    if len(chats_with_model) == 0:
        md += ["> No user in the %d chats read mentioned `%s`." % (len(chats), top_model), ""]
    md += ["## Which car models get the most interest", "",
           "### Reported by Analytics", "",
           report.md_table(["Model", "Clicks"], analytics_models or [["(none reported)", "-"]]), ""]
    if discrepancy:
        md += ["> **Data inconsistency.** %s" % discrepancy, ""]
    md += ["### Mentioned by customers in the Conversations tab", "",
           report.md_table(["Model", "Mentions"],
                           list(model_counts.most_common(15)) or [["(none found)", "-"]]), "",
           "## What people discuss", "",
           "**All %d messages read**" % len(sampled), "",
           report.md_table(["Topic", "Messages", "Share"],
                           [[t, c, "%.1f%%" % (100.0 * c / len(sampled))]
                            for t, c in topic_counts.most_common()]), ""]
    if around:
        md += ["**Only messages that name a vehicle (%d)**" % len(around), "",
               report.md_table(["Topic", "Messages", "Share"],
                               [[t, c, "%.1f%%" % (100.0 * c / len(around))]
                                for t, c in around_topics.most_common()]), ""]
    md += ["## Cross-check", "",
           "Models appearing in **both** Analytics and conversations: %s"
           % (", ".join(overlap) if overlap else "**none**"), ""]
    if not overlap:
        md += ["> The Analytics vehicle widget names models that never appear in the",
               "> conversations read, so the two data sources do not corroborate each",
               "> other. Figures from each source are reported separately rather than",
               "> merged.", ""]
    md += ["---", "",
           "All conversation figures come from the Conversations tab. Topic labels and",
           "model mentions are produced by the LLM from raw message text (Danish and",
           "English); all counts are computed in code from those labels.", ""]
    report.save("scenario_3_deep_dive", "\n".join(md), dealership)
    return result
