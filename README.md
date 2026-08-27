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
tests/                       parser and OTP-regex tests
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

```bash
python -m pytest tests/ -q
```
