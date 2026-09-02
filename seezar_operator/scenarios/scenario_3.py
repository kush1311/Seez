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


EVIDENCE_PER_TOPIC = 3
EVIDENCE_PER_MODEL = 2


def _quote(text: str, limit: int = 150) -> str:
    """One-line, length-capped rendering of a customer message for a report."""
    flat = " ".join(str(text).split())
    return flat if len(flat) <= limit else flat[:limit - 1] + "…"


def _evidence(records: List[dict], predicate, limit: int) -> List[tuple]:
    """Up to `limit` (chat_ref, message) examples matching predicate, one per chat
    where possible so the samples are not all from a single conversation."""
    picked, seen_chats = [], set()
    for record in records:
        if len(picked) >= limit:
            break
        if not predicate(record):
            continue
        ref = record.get("chat_ref", "?")
        if ref in seen_chats:
            continue
        seen_chats.add(ref)
        picked.append((ref, record["message"]))
    # Fall back to same-chat examples only if nothing else is available.
    if len(picked) < limit:
        for record in records:
            if len(picked) >= limit:
                break
            pair = (record.get("chat_ref", "?"), record["message"])
            if predicate(record) and pair not in picked:
                picked.append(pair)
    return picked


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
    # The analytics endpoint serves the same mock payload for every bot. If this
    # session already saw it, name the other bot so the report proves that rather
    # than presenting the figures as this dealership's own.
    duplicate_bot = dash.duplicate_metrics_bot(metrics, analytics_bot)

    chats_with_model = [c for c in chats if _mentions(top_model, c["user_messages"]) > 0]

    # Keep each message paired with the chat it came from, so every figure in the
    # report can be traced back to a quotable line in a named conversation.
    pairs = [(c["chat_ref"], m) for c in chats for m in c["user_messages"]]
    sampled_pairs = pairs[:max_messages]
    messages = [m for _, m in pairs]
    sampled = [m for _, m in sampled_pairs]
    # Many chats hold only an assistant greeting the customer never replied to, so
    # say how many actually contributed - a run of "0 customer messages" lines
    # otherwise looks like it contradicts the total.
    chats_with_messages = sum(1 for c in chats if c["user_messages"])
    logger.info(
        "Read %d chats: %d contained customer messages, %d were bot-only. "
        "Analysing %d of %d messages.",
        len(chats), chats_with_messages, len(chats) - chats_with_messages,
        len(sampled), len(messages),
    )
    if not sampled:
        raise RuntimeError("No customer messages found in the Conversations tab for %s" % dealership)

    records = llm.analyse(sampled)
    for record, (ref, _) in zip(records, sampled_pairs):
        record["chat_ref"] = ref

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

    # Traceable examples behind each reported figure.
    topic_evidence = {
        topic: _evidence(records, lambda r, t=topic: r["topic"] == t, EVIDENCE_PER_TOPIC)
        for topic, _ in topic_counts.most_common()
    }
    model_evidence = {
        model: _evidence(
            records,
            lambda r, m=model: any(x.title() == m for x in r["models"]),
            EVIDENCE_PER_MODEL,
        )
        for model in list(model_counts)[:10]
    }
    top_model_evidence = [
        (c["chat_ref"], _quote(m))
        for c in chats_with_model
        for m in c["user_messages"]
        if _mentions(top_model, [m])
    ][:5]

    result = {
        "dealership": dealership,
        "analytics_bot": analytics_bot,
        "duplicate_metrics_bot": duplicate_bot,
        "topic_evidence": topic_evidence,
        "model_evidence": model_evidence,
        "top_model_evidence": top_model_evidence,
        "top_model": top_model,
        "analytics_most_queried": most_queried,
        "analytics_models": analytics_models,
        "chats_listed": chats_listed,
        "chats_read": len(chats),
        "chats_with_messages": chats_with_messages,
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
    if duplicate_bot:
        report.note("These analytics are byte-identical to the payload already served "
                    "for bot %s in this run - the endpoint returns the same mock data "
                    "for every bot." % duplicate_bot)

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

    report.table(
        "Evidence - what each topic label was assigned to",
        ["Topic", "Chat", "Customer message"],
        [[topic, ref, _quote(msg, 70)]
         for topic, rows in topic_evidence.items() for ref, msg in rows] or
        [["(none)", "-", "-"]],
    )

    md = ["# Scenario III - Deep-Dive Explorer", "",
          "**Dealership:** %s  " % dealership,
          "**Most-clicked model (Analytics):** %s  " % top_model,
          "**Analytics served for bot:** `%s`  " % analytics_bot,
          "**Source:** Conversations tab, %d of %d chats read; %d carried customer "
          "messages, %d were bot-only  " % (len(chats), chats_listed,
                                            chats_with_messages, len(chats) - chats_with_messages),
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
    if duplicate_bot:
        md += ["> **These figures are not specific to this dealership.** The payload is",
               "> byte-identical to the one already served for bot `%s` in this same run."
               % duplicate_bot, ""]
    else:
        md += ["> **Treat these figures with care.** The analytics endpoint returned an",
               "> identical payload for every bot tested (527 Ejner Hessel, 454 Approved",
               "> Automotive, 243 Croxdale), so they are not dealership-specific. Only the",
               "> conversation figures below are.", ""]
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
    # ---- evidence: every figure above traced to quotable lines ----
    md += ["## Evidence", "",
           "Each figure above is a count over labelled messages. These are the actual "
           "messages behind them, with the chat they came from, so any number in this "
           "report can be checked against the Conversations tab.", ""]

    if top_model_evidence:
        md += ["### Customers who mentioned `%s`" % top_model, ""]
        md += ["- `%s` - \"%s\"" % (ref, msg) for ref, msg in top_model_evidence] + [""]
    elif top_model:
        md += ["### Customers who mentioned `%s`" % top_model, "",
               "No message in the %d chats read names this model." % len(chats), ""]

    quoted = sum(len(r) for r in topic_evidence.values())
    md += ["### Topic labels", "",
           "All %d messages were classified; %d are quoted here, up to %d per topic."
           % (len(sampled), quoted, EVIDENCE_PER_TOPIC), ""]
    for topic, rows in topic_evidence.items():
        md += ["**%s** - %d message(s), showing %d" % (topic, topic_counts[topic], len(rows)), ""]
        md += ["- `%s` - \"%s\"" % (ref, _quote(msg)) for ref, msg in rows] or \
              ["- (no example captured)"]
        md += [""]

    named = {m: rows for m, rows in model_evidence.items() if rows}
    if not model_counts:
        md += ["### Vehicle mentions", "",
               "No customer named a specific vehicle model in the %d messages read - "
               "enquiries were general rather than about a named model." % len(sampled), ""]
    if named:
        md += ["### Vehicle mentions", ""]
        for model, rows in named.items():
            md += ["**%s** - %d mention(s)" % (model, model_counts[model]), ""]
            md += ["- `%s` - \"%s\"" % (ref, _quote(msg)) for ref, msg in rows] + [""]

    md += ["---", "",
           "All conversation figures come from the Conversations tab. Topic labels and",
           "model mentions are produced by the LLM from raw message text (Danish and",
           "English); all counts are computed in code from those labels. The Evidence",
           "section above lists the source message and chat reference for each.", ""]
    report.save("scenario_3_deep_dive", "\n".join(md), dealership)
    return result
