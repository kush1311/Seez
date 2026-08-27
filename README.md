# Seezar Autonomous Operator

An autonomous operator for the [Seezar Dashboard](https://seezar-dashboard.seez.dev), covering Scenario II (Chat History ratio) and Scenario III (Deep-Dive Explorer).

## Setup

```bash
pip install -r requirements.txt
playwright install chromium
cp .env.example .env      # then fill in credentials
```

## Usage

```bash
python main.py --list                      # dealerships with an active bot
python main.py -s 2 -d "Ejner Hessel"      # Scenario II
python main.py -s 3 -d "Ejner Hessel"      # Scenario III
python main.py -s all -d "Norton Way"      # both, any dealership
streamlit run app.py                       # web UI
python evaluate.py --save                  # classifier accuracy
python -m pytest tests/ -q                 # 39 tests, offline
```

| Flag | Default | Purpose |
| --- | --- | --- |
| `--headless` | off | Run without a visible browser window |
| `--max-messages` | 200 | Messages sent to the LLM in Scenario III |
| `--max-chats` | 25 | Chats opened in the Conversations tab |
| `-v` | off | Debug logging |

Reports are written to `reports/`, timestamped per dealership.

## Architecture

```
main.py                      CLI
app.py                       Streamlit UI
evaluate.py                  classifier accuracy against hand labels
seezar_operator/
  config.py                  environment-driven settings
  dashboard.py               Playwright navigation, GraphQL capture
  chat_data.py               chat-history zip and CSV parsing
  llm.py                     OpenRouter client
  report.py                  terminal tables and Markdown export
  scenarios/scenario_2.py    messages per chat
  scenarios/scenario_3.py    deep-dive explorer
  utils/otp_fetcher.py       Gmail IMAP one-time-code reader
eval/gold_labels.json        60 hand-labelled messages
tests/                       39 tests
```

### Navigate the UI, read the network

The operator drives the dashboard as a person would, but the analytics charts are rendered to `<canvas>` and carry no readable text. Rather than OCR a chart or scrape the DOM, `dashboard.py` captures the GraphQL responses the page already makes and reads the exact `botMetrics` payload.

### Nothing is hardcoded

Dealership IDs, bot IDs and the dealership list are discovered at runtime from `getDealerships`: 316 dealerships with active bots. `--dealership` is matched case-insensitively against the live sidebar.

### The right bot, not the first one

Ejner Hessel hosts an internal HR assistant (`527`) and the customer-facing Seezar bot (`526`). The Conversations tab lists 24 chats for the first and 101 for the second, so `bots[0]` analyses the wrong population. The operator reads each bot's `botType` and prefers `seezar`. Analytics is served for `527` only, and `/analytics/526` redirects to it, so each report records which bot its figures describe.

### The LLM never produces a number

It assigns a topic label and extracts vehicle names from message text. Every count, share and ratio is computed in Python from those labels.

## Scenario II: messages per chat

Clicks Chat History, unzips the export, parses it, and divides total rows by unique `Chat Ref` values.

```
Total message rows   901
Unique Chat Refs     116
Messages per chat    7.77
```

Three properties of the export, all covered by `tests/test_chat_data.py`:

- UTF-8 with BOM, so the first column name is corrupted unless read as `utf-8-sig`.
- Messages contain embedded newlines: 2,719 physical lines but 901 rows. Counting lines gives roughly 23 messages per chat, three times the true figure.
- A dealership group ships one CSV per franchise. The Norton Way export holds `Norton Way.csv`, `Norton Way Peugeot.csv` and `Norton Way Citroen.csv`; reading only the first reports 4 rows across 1 chat, against a true 1,040 rows across 178 chats.

The export covers the dealership's full history while the dashboard's own figure is scoped to the selected date range, so the two are reported separately.

## Scenario III: deep-dive explorer

Reads the analytics vehicle figures, opens the Conversations tab and reads each chat, then analyses the full export.

How many users mentioned the most-clicked model:

| Measure | Value |
| --- | --- |
| Chats listed in the Conversations tab | 101 |
| Chats opened and read | 5 |
| Chats mentioning `Toyota Camry` | 0 |
| Mentions across the full export (447 messages) | 0 |

Which models get the most interest:

| Source | Result |
| --- | --- |
| Analytics `vehicleInsights` | Toyota Camry 60, Nissan Sunny 30 |
| Analytics `mostQueried` | Chevrolet Captiva |
| Customer conversations | Mercedes, Porsche, Ford Mondeo, Xpeng G9, Audi |

Two contradictions are flagged automatically:

1. `mostQueried` reports Chevrolet Captiva while the API's own `vehicleInsights` ranks Toyota Camry highest at 60 clicks; Captiva does not appear in the data.
2. No model named in Analytics appears in any customer message, so the two sources are reported separately rather than merged.

Topics are reported over all messages and, separately, over only those naming a vehicle.

### Why an LLM

The conversations mix Danish and English. `"Kan jeg bytte min gamle bil ind?"` is a trade-in enquiry and `"Hvad koster den om maneden med udbetaling?"` is financing rather than pricing; both are invisible to an English keyword list.

The operator does not click the most-clicked model because that element is a heading rather than a control: `H2`, `cursor: auto`, no click handler.

## Classifier evaluation

`eval/gold_labels.json` holds 60 messages sampled from the live export and labelled by hand.

| Taxonomy | Accuracy | Macro F1 |
| --- | --- | --- |
| Original | 71.7% (43/60) | 0.792 |
| With `parts_merchandise` | 95.0% (57/60) | 0.855 |

The first run put `inventory` at precision 1.00 and recall 0.46: the model was never wrong when it said inventory, but it would not file a branded t-shirt, a key cover or a set of tyres under it. In a car dealership inventory means vehicles, and 13 of the 17 errors were that one distinction, so the taxonomy was wrong rather than the model. `parts_merchandise` now scores 1.00 precision and recall over 17 messages.

That is also a business finding: a large share of this dealership's chat traffic concerns parts and merchandise rather than cars. Separately, one message in five is a request to speak to a human.

These figures measure agreement between the model and a single annotator, not objective truth. On 60 examples the 95% confidence interval around 95% is roughly 86-99%, so the result should not be read to one decimal place. Macro F1 averages only over classes present in the gold set. `financing` (support 1) and `specs` (support 2) are too small for a stable per-class score.

## Configuration

`.env`, see `.env.example`:

```ini
SEEZAR_EMAIL=
SEEZAR_PASSWORD=
GMAIL_USER=
GMAIL_APP_PASSWORD=
OPENROUTER_API_KEY=
OPENROUTER_MODEL=deepseek/deepseek-v4-flash-0731
```

Timeouts are environment-overridable: `CAPTURE_TIMEOUT_MS`, `CAPTURE_ATTEMPTS`, `DOWNLOAD_TIMEOUT_MS`, `DOWNLOAD_ATTEMPTS`.

The session is cached in `downloads/storage_state.json`; login with a Gmail-retrieved one-time code runs only when it expires.

## Reproducibility

Dependencies are pinned exactly; tested on Python 3.13. The Scenario III sample is drawn with a fixed seed and the model runs at `temperature: 0`, so repeat runs produce the same figures. The test suite is offline and runs in CI on every push.
