# Seezar Autonomous Operator

An autonomous operator for the [Seezar Dashboard](https://seezar-dashboard.seez.dev)
covering **Scenario II** (Chat History ratio) and **Scenario III** (Deep-Dive Explorer) —
one from each group, as the brief requires.

```bash
pip install -r requirements.txt
playwright install chromium

python main.py --list                        # dealerships with an active bot
python main.py -s 2 -d "Ejner Hessel"        # Scenario II
python main.py -s 3 -d "Ejner Hessel"        # Scenario III
python main.py -s all -d "Norton Way"        # both, any dealership
```

Add `--headless` to run without a browser window. Reports land in `reports/`.

### Web UI

```bash
streamlit run app.py
```

Pick any dealership from a live-loaded dropdown, run either scenario, watch the
operator's log stream as it works, and download the generated report. The CLI and
the UI call exactly the same functions — the UI adds no logic of its own.

---

## Architecture

```
main.py                      CLI
app.py                       Streamlit UI
evaluate.py                  scores the classifier against hand labels
seezar_operator/
  config.py                  env-driven settings; no hardcoded IDs
  dashboard.py               Playwright navigation + GraphQL interception
  chat_data.py               chat-history zip/CSV parsing
  llm.py                     OpenRouter client (topic + vehicle extraction)
  report.py                  terminal tables and Markdown export
  scenarios/
    scenario_2.py            messages per chat
    scenario_3.py            deep-dive explorer
  utils/otp_fetcher.py       Gmail IMAP one-time-code reader
eval/gold_labels.json        60 hand-labelled messages
tests/                       27 tests
```

Three decisions worth explaining on a call:

**Navigate the UI, read the network.** The operator clicks through the dashboard the
way a person would, but the analytics charts are rendered to `<canvas>` and carry no
readable text. Rather than OCR a chart or regex the DOM, `dashboard.py` listens to the
responses the SPA already makes and pulls the exact `botMetrics` JSON out of the wire.
Faithful to the brief, and exact instead of approximate.

**Nothing is hardcoded.** Dealership IDs, bot IDs and the dealership list are all
discovered at runtime from the `getDealerships` query — 316 dealerships with active
bots at the time of writing. `--dealership` is matched case-insensitively against the
live sidebar, so the operator works for any dealership without a code change.

**The right bot is chosen, not the first one.** Ejner Hessel hosts two bots: `527`
is an internal HR assistant (one of its conversations asks *"Hvordan registrerer jeg
sygdom?"* — how do I report sick leave) and `526` is the customer-facing Seezar bot.
Taking `bots[0]` analyses the HR bot. The operator reads each bot's `botType` from
`queryDealershipById` and prefers `seezar`.

**The LLM never produces a number.** It assigns a topic label and extracts vehicle
names from raw message text. Every count, percentage and ratio in every report is
computed in Python from those labels. A model cannot hallucinate a metric into a
report that way.

---

## Scenario II — Messages per Chat

Clicks **Chat History**, unzips the export, parses the CSV, and reports unique
`Chat Ref` values against total rows.

```
Total message rows   901
Unique Chat Refs     116
Messages per chat    7.77
```

Three traps in that export, all covered by `tests/test_chat_data.py`:

* it is UTF-8 **with BOM**, so the first column name is corrupted unless read as `utf-8-sig`;
* messages contain **embedded newlines** — the file has **2 719 physical lines but only
  901 rows**. Counting lines instead of parsing CSV gives ≈23 messages per chat, three
  times the true figure;
* a dealership **group ships one CSV per franchise**. The Norton Way export contains
  `Norton Way.csv`, `Norton Way Peugeot.csv` and `Norton Way Citroen.csv`. Reading only
  the first file reported 4 rows across 1 chat; reading all three gives the real figure
  of **1 040 rows across 178 chats — 5.84 messages per chat**.

The export covers the dealership's full history, while the dashboard's own
"Messages per chat" figure is scoped to the selected date range, so the two are
measuring different things and are deliberately reported separately.

---

## Scenario III — Deep-Dive Explorer

Reads the Analytics vehicle figures, then analyses what customers actually wrote.

**Which models get the most interest**

| Source | Result |
| --- | --- |
| Analytics `vehicleInsights` | Toyota Camry 60, Nissan Sunny 30 |
| Analytics `mostQueried` | Chevrolet Captiva |
| Customer conversations | Mercedes 13, Porsche 3, Ford Mondeo 3, Ford 3, Xpeng G9 3, Audi 3 |

The operator flags two contradictions automatically:

1. `mostQueried` reports **Chevrolet Captiva**, but the API's own `vehicleInsights`
   ranks **Toyota Camry** highest at 60 clicks — Captiva is not in the data at all.
2. **No model named in Analytics appears in a single customer message.** The two data
   sources do not corroborate each other, so they are reported separately rather than
   merged into one misleading ranking.

**What people discuss** (200 customer messages)

| Topic | Messages | Share |
| --- | --- | --- |
| inventory | 59 | 29.5 % |
| other | 41 | 20.5 % |
| human_handoff | 41 | 20.5 % |
| test_drive | 28 | 14.0 % |
| location | 19 | 9.5 % |
| service | 10 | 5.0 % |
| pricing | 1 | 0.5 % |
| specs | 1 | 0.5 % |

### Why an LLM, and how the taxonomy was derived

The conversations are **mixed Danish and English** (the bot language is `da-DK`).
Keyword matching cannot handle this — `"Kan jeg bytte min gamle bil ind?"` is a
trade-in enquiry and `"Hvad koster den om måneden med udbetaling?"` is financing,
not pricing. Both are classified correctly by the model and are invisible to any
English keyword list.

The first run used the brief's own example taxonomy — pricing, inventory, financing,
specs — and put **56 % of messages in `other`**. Inspecting that bucket showed the
dominant real intents were requests to reach a human, branch-location questions and
service bookings. Adding `service`, `location` and `human_handoff` cut `other` to
20.5 %. `service` independently corroborates the dashboard's own
`highestConvertingInquiryType: "serviceInquiry"`.

The standout business finding: **one message in five is a request to speak to a
human**, which says more about bot performance than any click metric on the page.

### A dealership can have more than one bot

Ejner Hessel hosts an internal HR assistant (`527`) and the customer-facing Seezar
bot (`526`). The Conversations tab lists **24** chats for the internal bot and
**101** for the customer-facing one, so picking `bots[0]` analyses the wrong
population entirely. The dashboard serves Analytics for `527` only and redirects
`/analytics/526` to it, so the operator records which bot each figure came from
rather than implying they describe the same one.

---

## Configuration

`.env` (see `.env.example`):

```ini
SEEZAR_EMAIL=...
SEEZAR_PASSWORD=...
GMAIL_USER=...              # for automated OTP retrieval
GMAIL_APP_PASSWORD=...      # 16-char Google app password
OPENROUTER_API_KEY=...
OPENROUTER_MODEL=deepseek/deepseek-v4-flash-0731
```

The session is cached in `downloads/storage_state.json`; login with OTP runs only
when it expires. Topic batches are sent concurrently, with retry on transient
failures and salvage-parsing for truncated JSON responses.

## Evaluating the classifier

Scenario III's topic labels come from a model, so their accuracy is measured rather
than assumed. `eval/gold_labels.json` holds 60 messages sampled from the live export
and labelled by hand.

```bash
python evaluate.py --save
```

| | Accuracy | Macro F1 |
| --- | --- | --- |
| Original taxonomy | 71.7 % (43/60) | 0.792 |
| With `parts_merchandise` | **95.0 % (57/60)** | **0.855** |

The first run put `inventory` at precision 1.00 but recall 0.46: when the model said
*inventory* it was always right, but it refused to file a Mercedes t-shirt, a key
cover or a set of tyres under it. In a car dealership *inventory* means vehicles,
and 13 of the 17 errors were that single distinction — the taxonomy was wrong, not
the model. Adding `parts_merchandise` took accuracy to 95 %, and that class now
scores 1.00 precision and recall over 17 messages.

That is also a business finding: **a large share of this dealership's chat traffic is
about parts and merchandise rather than cars.**

The three remaining disagreements are genuinely ambiguous — *"What is TRP?"* and two
Danish requests for articles about towing.

### How to read those numbers

They measure **agreement between the model and one annotator**, not objective truth.
There was no second labeller and no adjudication. On 60 examples the 95 % confidence
interval around 95 % is roughly 86–99 %, so the figure should not be read to one
decimal place. Macro F1 averages only over classes present in the gold set; `pricing`
was predicted once but never appears in it, so its F1 is undefined rather than zero.
Classes such as `financing` (support 1) and `specs` (support 2) are too small to
support a stable per-class score.

## Reproducibility

Dependencies are pinned exactly (`requirements.txt`); tested on Python 3.13.
The Scenario III sample is drawn with a fixed seed, and the model runs at
`temperature: 0`, so repeat runs produce the same figures.

```bash
python -m pytest tests/ -q
```
