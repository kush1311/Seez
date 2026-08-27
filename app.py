from __future__ import annotations

import logging
import queue
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pandas as pd
import streamlit as st

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))

from seezar_operator.config import OPENROUTER_MODEL, REPORTS_DIR
from seezar_operator.dashboard import Dashboard
from seezar_operator.scenarios import scenario_2, scenario_3

st.set_page_config(page_title="Seezar Autonomous Operator", page_icon=":material/smart_toy:", layout="wide")


def _isolated(fn, *args, **kwargs):
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(fn, *args, **kwargs).result()


def _run_with_logs(fn, *args, **kwargs):
    log_q: queue.Queue = queue.Queue()

    class Handler(logging.Handler):
        def emit(self, record):
            log_q.put(self.format(record))

    handler = Handler()
    handler.setFormatter(logging.Formatter("%(asctime)s  %(message)s", datefmt="%H:%M:%S"))
    root = logging.getLogger("seezar")
    root.setLevel(logging.INFO)
    root.addHandler(handler)

    box: dict = {}

    def target():
        try:
            box["value"] = fn(*args, **kwargs)
        except BaseException as exc:
            box["error"] = exc

    worker = threading.Thread(target=target, daemon=True)
    worker.start()

    lines: list[str] = []
    placeholder = st.empty()
    while worker.is_alive() or not log_q.empty():
        try:
            lines.append(log_q.get(timeout=0.3))
            placeholder.code("\n".join(lines[-14:]), language="text")
        except queue.Empty:
            continue
    worker.join()
    root.removeHandler(handler)

    if "error" in box:
        raise box["error"]
    return box["value"]


@st.cache_resource(show_spinner=False)
def load_dealerships() -> dict:
    def work():
        dash = Dashboard(headless=True).start()
        try:
            dash.open()
            return dash.dealerships()
        finally:
            dash.close()
    return _isolated(work)


st.sidebar.title("Seezar Operator")
st.sidebar.caption("Scenario II + III against the live dashboard")

if st.sidebar.button("Reload dealership list"):
    load_dealerships.clear()

try:
    with st.sidebar.status("Discovering dealerships...", expanded=False):
        dealers = load_dealerships()
except Exception as exc:
    st.sidebar.error("Could not reach the dashboard: %s" % exc)
    st.error(
        "The operator could not sign in. Check `SEEZAR_EMAIL`, `GMAIL_USER` and "
        "`GMAIL_APP_PASSWORD` in `.env`, then reload."
    )
    st.stop()

names = sorted(dealers)
default = names.index("Ejner Hessel") if "Ejner Hessel" in names else 0
dealership = st.sidebar.selectbox("Dealership", names, index=default,
                                  help="%d dealerships with an active bot" % len(names))
scenario = st.sidebar.radio("Scenario", ["II - Messages per chat",
                                         "III - Deep-dive explorer",
                                         "Both"], index=0)
max_messages = st.sidebar.slider("Messages to classify (Scenario III)", 25, 450, 200, step=25)
show_browser = st.sidebar.checkbox("Show the browser while it runs", value=False,
                                   help="Watch the operator navigate; useful for a live demo")
st.sidebar.caption("Model: `%s`" % OPENROUTER_MODEL)

resolved = dealers[dealership]
st.sidebar.caption("dealership id `%s` - bot `%s`" % (resolved["id"], resolved["bots"][0]))


def show_scenario_2(stats: dict) -> None:
    st.subheader("Scenario II - Messages per Chat")
    a, b, c = st.columns(3)
    a.metric("Total message rows", "{:,}".format(stats["total_rows"]))
    b.metric("Unique Chat Refs", "{:,}".format(stats["unique_chat_refs"]))
    c.metric("Messages per chat", stats["messages_per_chat"])
    st.code(
        "messages per chat = total rows / unique Chat Refs\n"
        "                  = %d / %d\n"
        "                  = %s" % (stats["total_rows"], stats["unique_chat_refs"],
                                    stats["messages_per_chat"]),
        language="text",
    )
    st.caption("Parsed from: " + ", ".join("`%s`" % s for s in stats["source_files"]))
    if len(stats["source_files"]) > 1:
        st.info(
            "This dealership group ships one CSV per franchise. All %d were combined - "
            "reading only the first would report a single branch as the whole group."
            % len(stats["source_files"])
        )


def show_scenario_3(res: dict) -> None:
    st.subheader("Scenario III - Deep-Dive Explorer")
    st.caption("%d of %d customer messages classified"
               % (res["messages_analysed"], res["messages_total"]))

    left, right = st.columns(2)
    with left:
        st.markdown("**Models most clicked** (Analytics)")
        st.dataframe(pd.DataFrame(res["analytics_models"], columns=["Model", "Clicks"]),
                     hide_index=True, use_container_width=True)
    with right:
        st.markdown("**Models mentioned** (conversations)")
        st.dataframe(
            pd.DataFrame(list(res["model_counts"].items()), columns=["Model", "Mentions"]),
            hide_index=True, use_container_width=True,
        )

    if res["discrepancy"]:
        st.warning(res["discrepancy"])
    if not res["overlap"]:
        st.warning(
            "No model named in Analytics appears in any customer message, so the two "
            "sources are reported separately rather than merged."
        )

    st.markdown("**What customers discuss**")
    topics = pd.DataFrame(list(res["topic_counts"].items()), columns=["Topic", "Messages"])
    topics["Share"] = (100 * topics["Messages"] / res["messages_analysed"]).round(1).astype(str) + "%"
    chart, table = st.columns([2, 1])
    chart.bar_chart(topics.set_index("Topic")["Messages"], height=300)
    table.dataframe(topics, hide_index=True, use_container_width=True)


def offer_report(stem: str, label: str) -> None:
    matches = sorted(REPORTS_DIR.glob("%s*.md" % stem), key=lambda p: p.stat().st_mtime)
    if matches:
        newest = matches[-1]
        st.download_button(label, newest.read_text(encoding="utf-8"),
                           file_name=newest.name, mime="text/markdown")


st.title("Seezar Autonomous Operator")
st.caption(
    "Navigates the live dashboard, captures the analytics payload off the wire, "
    "and analyses the real chat-history export. Every figure is computed in code - "
    "the model only labels text."
)

if st.button("Run operator", type="primary"):
    run_2 = scenario in ("II - Messages per chat", "Both")
    run_3 = scenario in ("III - Deep-dive explorer", "Both")
    try:
        def work():
            dash = Dashboard(headless=not show_browser).start()
            try:
                dash.open()
                out = {}
                if run_2:
                    out["s2"] = scenario_2.run(dealership, dash=dash)
                if run_3:
                    out["s3"] = scenario_3.run(dealership, dash=dash, max_messages=max_messages)
                return out
            finally:
                dash.close()

        with st.status("Running against the live dashboard...", expanded=True):
            results = _run_with_logs(lambda: _isolated(work))
        st.success("Finished")

        if "s2" in results:
            show_scenario_2(results["s2"])
            offer_report("scenario_2_messages_per_chat", "Download Scenario II report")
        if "s3" in results:
            st.divider()
            show_scenario_3(results["s3"])
            offer_report("scenario_3_deep_dive", "Download Scenario III report")

    except Exception as exc:
        st.error("%s: %s" % (type(exc).__name__, exc))
else:
    st.info("Pick a dealership on the left, then run the operator.")
