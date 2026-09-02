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

# Streamlit reloads this file on each rerun but leaves imported modules in
# sys.modules for the life of the process, so edits under seezar_operator/ would
# otherwise keep running until the server was restarted. Dropping them here
# forces the imports below to pick up whatever is on disk now.
for _stale in [n for n in sys.modules if n == "seezar_operator" or n.startswith("seezar_operator.")]:
    del sys.modules[_stale]

from seezar_operator.config import OPENROUTER_MODEL, REPORTS_DIR
from seezar_operator.dashboard import Dashboard
from seezar_operator.scenarios import scenario_2, scenario_3
from seezar_operator.scenarios.scenario_3 import EVIDENCE_PER_TOPIC

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
DEALER_KEY = "dealership_choice"


def parent_of(name: str):
    """The unit that owns this one's Chat History export, if it is not itself a parent."""
    parent_id = dealers.get(name, {}).get("parent")
    if not parent_id:
        return None
    return next((n for n, v in dealers.items() if v["id"] == parent_id), None)


def dealer_label(name: str) -> str:
    # Franchises stay in the list - they are 75% of it and Scenario III works on
    # them - but the marker says up front that Scenario II will not.
    return name + ("  (franchise)" if parent_of(name) else "")


dealership = st.sidebar.selectbox(
    "Dealership", names, index=default, key=DEALER_KEY, format_func=dealer_label,
    help="%d units with an active bot. Franchises support Scenario III; their "
         "Chat History export lives on the parent." % len(names),
)
scenario = st.sidebar.radio("Scenario", ["II - Messages per chat",
                                         "III - Deep-dive explorer",
                                         "Both"], index=0)
max_messages = st.sidebar.slider("Messages to classify (Scenario III)", 25, 450, 200, step=25)
max_chats = st.sidebar.slider("Chats to open in Conversations tab", 5, 50, 25, step=5)
show_browser = st.sidebar.checkbox("Show the browser while it runs", value=False,
                                   help="Watch the operator navigate; useful for a live demo")
st.sidebar.caption("Model: `%s`" % OPENROUTER_MODEL)

resolved = dealers[dealership]
st.sidebar.caption("%s - id `%s` - bot `%s`"
                   % (resolved.get("unit") or "unit", resolved["id"], resolved["bots"][0]))

parent_name = parent_of(dealership)
if parent_name:
    st.sidebar.warning(
        "**Scenario II is not available here.** %s is a %s; the dashboard offers the "
        "Chat History export only on its parent, **%s**, whose archive already "
        "contains this unit's CSV. Scenario III works normally."
        % (dealership, resolved.get("unit") or "franchise", parent_name)
    )

    def _switch_to_parent() -> None:
        st.session_state[DEALER_KEY] = parent_name

    st.sidebar.button("Switch to %s" % parent_name, on_click=_switch_to_parent)


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
        if res["model_counts"]:
            st.dataframe(
                pd.DataFrame(list(res["model_counts"].items()), columns=["Model", "Mentions"]),
                hide_index=True, use_container_width=True,
            )
        else:
            # An empty grid reads as a failure; the real result is "nobody named one".
            st.info(
                "No customer named a specific vehicle model in the %d messages read. "
                "Enquiries here were general - used cars, fuel type, location - rather "
                "than about a named model."
                % res["messages_analysed"]
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

    # Most chats are an assistant greeting nobody replied to. Saying so stops the
    # message total looking like it contradicts the number of chats opened.
    bot_only = res["chats_read"] - res.get("chats_with_messages", res["chats_read"])
    if bot_only:
        st.caption(
            "%d of the %d chats read contained customer messages; %d held only bot "
            "replies with no customer text." % (res.get("chats_with_messages", 0),
                                                res["chats_read"], bot_only)
        )

    st.markdown("**Evidence - the messages behind these counts**")
    evidence_rows = [
        {"Topic": "%s (%d of %d shown)" % (topic, len(rows), res["topic_counts"][topic]),
         "Chat": ref, "Customer message": msg}
        for topic, rows in res.get("topic_evidence", {}).items()
        for ref, msg in rows
    ]
    # State the sampling explicitly: "13 classified" next to 7 quotes otherwise
    # looks like a mismatch rather than a deliberate cap.
    st.caption(
        "All %d messages were classified. This is a sample of up to %d per topic - "
        "%d quoted below. The model assigns each label; the counts above are computed "
        "in code from those labels, and every chat reference can be opened in the "
        "dashboard's Conversations tab to check a figure."
        % (res["messages_analysed"], EVIDENCE_PER_TOPIC, len(evidence_rows))
    )
    if evidence_rows:
        st.dataframe(pd.DataFrame(evidence_rows), hide_index=True, use_container_width=True)
    else:
        st.info("No messages were classified, so there is nothing to quote.")

    top_ev = res.get("top_model_evidence") or []
    if top_ev:
        st.markdown("**Customers who mentioned `%s`**" % res["top_model"])
        st.dataframe(pd.DataFrame(top_ev, columns=["Chat", "Customer message"]),
                     hide_index=True, use_container_width=True)
    elif res.get("top_model"):
        st.caption("No message in the %d chats read names `%s`."
                   % (res["chats_read"], res["top_model"]))


def offer_report(stem: str, label: str) -> None:
    matches = sorted(REPORTS_DIR.glob("%s*.md" % stem), key=lambda p: p.stat().st_mtime)
    if matches:
        newest = matches[-1]
        st.download_button(label, newest.read_text(encoding="utf-8"),
                           file_name=newest.name, mime="text/markdown")


st.title("Seezar Autonomous Operator")
st.caption(
    "Navigates the live dashboard and captures the analytics payload off the wire. "
    "Scenario II parses the Chat History archive; Scenario III reads the Conversations "
    "tab. All counts are computed in code - the model only labels text."
)

if st.button("Run operator", type="primary"):
    st.session_state.pop("results", None)
    st.session_state.pop("error", None)
    run_2 = scenario in ("II - Messages per chat", "Both")
    run_3 = scenario in ("III - Deep-dive explorer", "Both")

    # Skip Scenario II on a franchise rather than spending 20s discovering the
    # export button is absent. "Both" still runs Scenario III, which works here.
    if run_2 and parent_name:
        run_2 = False
        if not run_3:
            st.session_state["error"] = (
                "Scenario II needs the Chat History export, which %s does not have. "
                "Run it on the parent, %s." % (dealership, parent_name)
            )
        else:
            st.info("Skipping Scenario II - %s is a franchise. Running Scenario III only."
                    % dealership)
    try:
        def work():
            dash = Dashboard(headless=not show_browser).start()
            try:
                dash.open()
                out = {}
                if run_2:
                    out["s2"] = scenario_2.run(dealership, dash=dash)
                if run_3:
                    out["s3"] = scenario_3.run(dealership, dash=dash, max_messages=max_messages,
                                               max_chats=max_chats)
                return out
            finally:
                dash.close()

        with st.status("Running against the live dashboard...", expanded=True):
            st.session_state["results"] = _run_with_logs(lambda: _isolated(work))
    except Exception as exc:
        st.session_state["error"] = "%s: %s" % (type(exc).__name__, exc)

# Rendered outside the button block. Streamlit reruns the whole script on every
# interaction, and the button reads False on a rerun - results held inside it
# would disappear the moment a download button was clicked.
if st.session_state.get("error"):
    st.error(st.session_state["error"])

results = st.session_state.get("results")
if results:
    st.success("Finished")
    if "s2" in results:
        show_scenario_2(results["s2"])
        offer_report("scenario_2_messages_per_chat", "Download Scenario II report")
    if "s3" in results:
        st.divider()
        show_scenario_3(results["s3"])
        offer_report("scenario_3_deep_dive", "Download Scenario III report")
elif not st.session_state.get("error"):
    st.info("Pick a dealership on the left, then run the operator.")
