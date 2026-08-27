from __future__ import annotations

import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Iterable, Sequence

from seezar_operator.config import REPORTS_DIR

logger = logging.getLogger("seezar.report")

try:
    from rich.console import Console
    from rich.table import Table
    _console = Console()
except ImportError:
    _console = None


def header(title: str) -> None:
    line = "=" * max(len(title) + 4, 60)
    if _console:
        _console.print("\n[bold cyan]%s[/bold cyan]" % line)
        _console.print("[bold white]  %s[/bold white]" % title)
        _console.print("[bold cyan]%s[/bold cyan]" % line)
    else:
        print("\n%s\n  %s\n%s" % (line, title, line))


def table(title: str, columns: Sequence[str], rows: Iterable[Sequence[object]]) -> None:
    rows = [[str(c) for c in r] for r in rows]
    if _console:
        t = Table(title=title, header_style="bold magenta")
        for c in columns:
            t.add_column(str(c))
        for r in rows:
            t.add_row(*r)
        _console.print(t)
        return
    widths = [max(len(str(columns[i])), *(len(r[i]) for r in rows)) if rows else len(columns[i])
              for i in range(len(columns))]
    fmt = "  ".join("{:<%d}" % w for w in widths)
    print("\n" + title)
    print(fmt.format(*columns))
    print(fmt.format(*["-" * w for w in widths]))
    for r in rows:
        print(fmt.format(*r))


def note(msg: str) -> None:
    if _console:
        _console.print("[yellow]! %s[/yellow]" % msg)
    else:
        print("! %s" % msg)


def md_table(columns: Sequence[str], rows: Iterable[Sequence[object]]) -> str:
    out = ["| " + " | ".join(str(c) for c in columns) + " |",
           "| " + " | ".join("---" for _ in columns) + " |"]
    for r in rows:
        out.append("| " + " | ".join(str(c) for c in r) + " |")
    return "\n".join(out)


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "unknown"


def save(stem: str, content: str, dealership: str = "") -> Path:
    # Per-dealership and timestamped: a fixed filename means running a second
    # dealership silently destroys the first one's results.
    parts = [stem] + ([_slug(dealership)] if dealership else [])
    parts.append(datetime.now().strftime("%Y%m%d-%H%M%S"))
    path = REPORTS_DIR / ("%s.md" % "_".join(parts))
    path.write_text(content, encoding="utf-8")
    logger.info("Report written to %s", path)
    if _console:
        _console.print("[green]Report saved:[/green] %s" % path)
    else:
        print("Report saved: %s" % path)
    return path
