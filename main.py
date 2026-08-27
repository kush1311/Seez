from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))

from seezar_operator import report
from seezar_operator.dashboard import Dashboard
from seezar_operator.scenarios import scenario_2, scenario_3


def _force_utf8_stdout() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    logging.getLogger("urllib3").setLevel(logging.WARNING)


def main() -> int:
    ap = argparse.ArgumentParser(description="Seezar Autonomous Operator")
    ap.add_argument("-s", "--scenario", choices=["2", "3", "all"], default="all",
                    help="2 = messages per chat, 3 = deep-dive explorer")
    ap.add_argument("-d", "--dealership", default="Ejner Hessel",
                    help="Dealership name; matched case-insensitively against the live sidebar")
    ap.add_argument("--list", action="store_true", help="List dealerships that have a bot, then exit")
    ap.add_argument("--headless", action="store_true", help="Run without a visible browser window")
    ap.add_argument("--max-messages", type=int, default=scenario_3.MAX_MESSAGES,
                    help="Cap on customer messages sent to the LLM in scenario 3")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    _force_utf8_stdout()
    _setup_logging(args.verbose)

    dash = Dashboard(headless=args.headless or None).start()
    try:
        dash.open()

        if args.list:
            table = dash.dealerships()
            report.header("Dealerships with an active bot (%d)" % len(table))
            report.table("Discovered live from the dashboard", ["Dealership", "ID", "Bot"],
                         [[n, v["id"], v["bots"][0]] for n, v in sorted(table.items())])
            return 0

        if args.scenario in ("2", "all"):
            scenario_2.run(args.dealership, dash=dash)
        if args.scenario in ("3", "all"):
            scenario_3.run(args.dealership, dash=dash, max_messages=args.max_messages)
        return 0

    except Exception as exc:
        logging.getLogger("seezar").error("%s: %s", type(exc).__name__, exc)
        return 1
    finally:
        dash.close()


if __name__ == "__main__":
    raise SystemExit(main())
