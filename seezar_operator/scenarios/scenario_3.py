from __future__ import annotations

import logging
import random
import re
from collections import Counter
from typing import List, Optional

from seezar_operator import llm, report
from seezar_operator.chat_data import customer_messages, load_export
from seezar_operator.dashboard import Dashboard

logger = logging.getLogger("seezar.scenario3")

MAX_MESSAGES = 200
MAX_CHATS = 25
SAMPLE_SEED = 42


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
    """Count texts naming the model. Matches the full name or any distinctive
    word in it, so "Camry" counts towards "Toyota Camry"."""
    if not model:
        return 0
    parts = [p for p in re.split(r"\s+", model) if len(p) > 2]
    patterns = [re.escape(model)] + [re.escape(p) for p in parts]
    rx = re.compile("|".join(patterns), re.IGNORECASE)
    return sum(1 for t in texts if rx.search(t))


def run(dealership: str, dash: Optional[Dashboard] = None,
        max_messages: int = MAX_MESSAGES, max_chats: int = MAX_CHATS) -> dict:
    own = dash is None
    dash = dash or Dashboard().start().open()
    try:
        metrics = dash.bot_metrics(dealership)
        chats = dash.conversations(dealership, max_chats=max_chats)
        zip_path = dash.download_chat_history(dealership)
    finally:
        if own:
            dash.close()

    most_queried, analytics_models = _vehicle_insights(metrics)
    top_model = _top_model(most_queried, analytics_models)

    # Conversations tab: how many of those users mentioned the top model.
    chats_with_model = [
        c for c in chats if _mentions(top_model, c["user_messages"]) > 0
    ]

    df = load_export(zip_path)
    msgs = customer_messages(df)
    # Random, seeded: msgs[:n] would sample only the oldest conversations and
    # every share below would describe those rather than the dealership.
    sampled = (msgs if len(msgs) <= max_messages
               else random.Random(SAMPLE_SEED).sample(msgs, max_messages))
    logger.info("Analysing %d of %d customer messages", len(sampled), len(msgs))

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
        "top_model": top_model,
        "analytics_most_queried": most_queried,
        "analytics_models": analytics_models,
        "chats_reviewed": len(chats),
        "chats_mentioning_top_model": len(chats_with_model),
        "export_mentions_top_model": _mentions(top_model, msgs),
        "messages_analysed": len(sampled),
        "messages_total": len(msgs),
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
            ["Chats reviewed in Conversations tab", len(chats)],
            ["Chats mentioning this model", len(chats_with_model)],
            ["Mentions across the full export (%d messages)" % len(msgs),
             result["export_mentions_top_model"]],
        ],
    )
    report.table("Conversations - models customers actually mentioned (%d messages)" % len(sampled),
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
          "**Messages analysed:** %d of %d, random sample (seed %d)  "
          % (len(sampled), len(msgs), SAMPLE_SEED),
          "**Topic model:** `%s` via OpenRouter" % llm.OPENROUTER_MODEL, "",
          "## How many users mentioned this model", "",
          report.md_table(
              ["Measure", "Value"],
              [["Chats opened in the Conversations tab", len(chats)],
               ["Chats mentioning `%s`" % top_model, len(chats_with_model)],
               ["Mentions across the full export (%d messages)" % len(msgs),
                result["export_mentions_top_model"]]],
          ), ""]
    if len(chats_with_model) == 0:
        md += ["> No user in the Conversations tab mentioned `%s`, and it does not "
               "appear anywhere in the %d-message export either." % (top_model, len(msgs)), ""]
    md += ["## Which car models get the most interest", "",
           "### Reported by Analytics", "",
           report.md_table(["Model", "Clicks"], analytics_models or [["(none reported)", "-"]]), ""]
    if discrepancy:
        md += ["> **Data inconsistency.** %s" % discrepancy, ""]
    md += ["### Mentioned by customers in conversations", "",
           report.md_table(["Model", "Mentions"],
                           list(model_counts.most_common(15)) or [["(none found)", "-"]]), "",
           "## What people discuss", "",
           "**All sampled messages**", "",
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
               "> conversation export, so the two data sources do not corroborate",
               "> each other. Figures from each source are reported separately rather",
               "> than merged.", ""]
    md += ["---", "",
           "Topic labels and model mentions are produced by the LLM from raw message",
           "text (Danish and English). All counts are computed in code from those",
           "labels - no figure in this report is generated by the model.", ""]
    report.save("scenario_3_deep_dive", "\n".join(md), dealership)
    return result
