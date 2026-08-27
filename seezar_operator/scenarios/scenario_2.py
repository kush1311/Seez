from __future__ import annotations

import logging
from typing import Optional

from seezar_operator import report
from seezar_operator.chat_data import load_export, messages_per_chat
from seezar_operator.dashboard import Dashboard

logger = logging.getLogger("seezar.scenario2")


def run(dealership: str, dash: Optional[Dashboard] = None) -> dict:
    own = dash is None
    dash = dash or Dashboard().start().open()
    try:
        zip_path = dash.download_chat_history(dealership)
    finally:
        if own:
            dash.close()

    df = load_export(zip_path)
    stats = messages_per_chat(df)
    stats["dealership"] = dealership
    stats["source_files"] = df.attrs.get("source_csvs") or [zip_path.name]

    report.header("Scenario II - Messages per Chat: %s" % dealership)
    report.table(
        "Chat History Export",
        ["Metric", "Value"],
        [
            ["Source file(s)", ", ".join(stats["source_files"])],
            ["Total message rows", stats["total_rows"]],
            ["Unique Chat Refs", stats["unique_chat_refs"]],
            ["Messages per chat", stats["messages_per_chat"]],
        ],
    )
    report.note(
        "Row count comes from a real CSV parse, not a line count - messages "
        "contain embedded newlines, so the file has far more lines than rows."
    )

    md = "\n".join([
        "# Scenario II - Messages per Chat",
        "",
        "**Dealership:** %s  " % dealership,
        "**Source:** %s (from the Chat History zip)"
        % ", ".join("`%s`" % s for s in stats["source_files"]),
        "",
        report.md_table(
            ["Metric", "Value"],
            [
                ["Total message rows", stats["total_rows"]],
                ["Unique `Chat Ref` values", stats["unique_chat_refs"]],
                ["**Messages per chat**", "**%s**" % stats["messages_per_chat"]],
            ],
        ),
        "",
        "```",
        "messages per chat = total rows / unique Chat Refs",
        "                  = %d / %d" % (stats["total_rows"], stats["unique_chat_refs"]),
        "                  = %s" % stats["messages_per_chat"],
        "```",
        "",
        "> The CSV is UTF-8 with BOM and contains embedded newlines inside message",
        "> text, so the file has more physical lines than data rows. The row count",
        "> above comes from a real CSV parse.",
    ])
    report.save("scenario_2_messages_per_chat", md, dealership)
    return stats
