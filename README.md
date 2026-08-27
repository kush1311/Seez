# Seezar Autonomous Operator

An autonomous browser operator for the [Seezar Dashboard](https://seezar-dashboard.seez.dev). It signs in, navigates to a dealership, and produces two reports: the ratio of chat messages to conversations (Scenario II) and an analysis of vehicle interest and discussion topics (Scenario III).

## Requirements

- Python 3.13
- A Seezar Dashboard account
- A Gmail account with an app password, for automated one-time-code retrieval
- An OpenRouter API key

## Installation

```bash
pip install -r requirements.txt
playwright install chromium
cp .env.example .env
```

## Configuration

Settings are read from `.env`.

| Variable | Required | Default |
| --- | --- | --- |
| `SEEZAR_EMAIL` | yes | |
| `SEEZAR_PASSWORD` | yes | |
| `GMAIL_USER` | yes | |
| `GMAIL_APP_PASSWORD` | yes | |
| `OPENROUTER_API_KEY` | Scenario III only | |
| `OPENROUTER_MODEL` | no | `deepseek/deepseek-v4-flash-0731` |
| `SEEZAR_DASHBOARD_URL` | no | `https://seezar-dashboard.seez.dev` |
| `HEADLESS_BROWSER` | no | `false` |
| `CAPTURE_TIMEOUT_MS` | no | `75000` |
| `CAPTURE_ATTEMPTS` | no | `3` |
| `DOWNLOAD_TIMEOUT_MS` | no | `300000` |
| `DOWNLOAD_ATTEMPTS` | no | `2` |

The authenticated session is cached in `downloads/storage_state.json`. A full sign-in, including one-time-code retrieval over IMAP, runs only when that session expires.

## Usage

```bash
python main.py --list                      # dealerships with an active bot
python main.py -s 2 -d "Ejner Hessel"      # Scenario II
python main.py -s 3 -d "Ejner Hessel"      # Scenario III
python main.py -s all -d "Norton Way"      # both
```

| Flag | Default | Description |
| --- | --- | --- |
| `-s`, `--scenario` | `all` | `2`, `3` or `all` |
| `-d`, `--dealership` | `Ejner Hessel` | Matched case-insensitively against the live sidebar |
| `--list` | | List dealerships with an active bot and exit |
| `--headless` | off | Run without a visible browser window |
| `--max-messages` | `200` | Messages classified in Scenario III |
| `--max-chats` | `25` | Chats opened in the Conversations tab |
| `-v`, `--verbose` | off | Debug logging |

Reports are written to `reports/` as Markdown, named per dealership and timestamped.

A Streamlit interface exposes the same operations:

```bash
streamlit run app.py
```

Streamlit reloads `app.py` on each interaction but not the imported `seezar_operator`
modules, which are held in `sys.modules` for the life of the process. Restart the
server after editing anything outside `app.py`.

## Architecture

```
main.py                      command-line interface
app.py                       Streamlit interface
evaluate.py                  classifier evaluation harness
seezar_operator/
  config.py                  environment-driven settings
  dashboard.py               browser control and GraphQL response capture
  chat_data.py               chat-history archive parsing
  llm.py                     OpenRouter client
  report.py                  terminal tables and Markdown output
  scenarios/scenario_2.py    messages per chat
  scenarios/scenario_3.py    deep-dive explorer
  utils/otp_fetcher.py       one-time-code retrieval over IMAP
eval/gold_labels.json        annotated evaluation set
tests/                       unit tests
```

### Data acquisition

The dashboard renders its analytics charts to `<canvas>`, so the underlying values are not present in the DOM. The operator navigates the interface with Playwright and concurrently records the GraphQL responses the application issues, reading values from the `botMetrics` payload. Conversation content is obtained the same way, from `seezarChats` and `getUserChatHistory` as each chat is opened.

### Identifier resolution

Dealership identifiers, bot identifiers and the dealership list are resolved at runtime from the `getDealerships` query, which returns 316 dealerships with an active bot. Dealership names supplied on the command line are matched case-insensitively, with ambiguous matches rejected.

### Bot selection

A dealership may host multiple bots. Ejner Hessel has an internal support assistant (`527`) and a customer-facing bot (`526`), whose Conversations tabs contain 24 and 101 chats respectively. The operator reads each bot's `botType` from `queryDealershipById` and selects the customer-facing one. Analytics is served only for `527`, and `/analytics/526` redirects to it, so each report records the bot its figures describe.

### Metric derivation

The language model assigns a topic label to each message and extracts any vehicle names it contains. All counts, shares and ratios are computed from those labels in Python.

## Scenario II: messages per chat

The operator downloads the Chat History archive, parses the CSV files it contains, and divides the total row count by the number of distinct `Chat Ref` values.

Result for Ejner Hessel:

```
Total message rows   901
Unique Chat Refs     116
Messages per chat    7.77
```

The archive has three properties that affect parsing, covered by `tests/test_chat_data.py`:

- It is UTF-8 with a byte-order mark, which corrupts the first column name unless read as `utf-8-sig`.
- Message text contains embedded newlines. The Ejner Hessel file has 2,719 physical lines and 901 rows; counting lines yields approximately 23 messages per chat against a true 7.77.
- A dealership group ships one file per franchise. The Norton Way archive contains `Norton Way.csv`, `Norton Way Peugeot.csv` and `Norton Way Citroen.csv`, totalling 1,040 rows across 178 chats.

The archive covers a dealership's full history, whereas the dashboard's own messages-per-chat figure is scoped to the selected date range. The two are reported separately.

## Scenario III: deep-dive explorer

The operator reads the analytics vehicle figures, opens the Conversations tab and reads each chat, then classifies messages from the full export.

Mentions of the most-clicked model:

| Measure | Value |
| --- | --- |
| Chats listed in the Conversations tab | 101 |
| Chats opened and read | 5 |
| Chats mentioning `Toyota Camry` | 0 |
| Mentions across the full export (447 messages) | 0 |

Vehicle interest by source:

| Source | Result |
| --- | --- |
| Analytics `vehicleInsights` | Toyota Camry 60, Nissan Sunny 30 |
| Analytics `mostQueried` | Chevrolet Captiva |
| Customer conversations | Mercedes, Porsche, Ford Mondeo, Xpeng G9, Audi |

Two inconsistencies in the source data are reported rather than reconciled:

1. `mostQueried` returns Chevrolet Captiva, while `vehicleInsights` in the same payload ranks Toyota Camry highest at 60 clicks. Chevrolet Captiva does not appear in `vehicleInsights`.
2. No vehicle named in the analytics payload appears in any customer message, so the two sources are presented independently.

Topic distribution is reported over all sampled messages and over the subset that names a vehicle.

Classification is performed by a language model because the conversations mix Danish and English: `"Kan jeg bytte min gamle bil ind?"` is a trade-in enquiry, and `"Hvad koster den om maneden med udbetaling?"` concerns financing rather than pricing. Neither is reachable by English keyword matching.

The brief describes clicking the most-clicked model. That element is an `H2` heading with `cursor: auto` and no click handler, so the value is read from the analytics payload instead.

## Evaluation

`eval/gold_labels.json` contains 60 messages drawn from the live export and annotated by hand. `python evaluate.py --save` classifies them and reports accuracy, macro F1, per-class precision and recall, and every disagreement.

| Taxonomy | Accuracy | Macro F1 |
| --- | --- | --- |
| Initial | 71.7% (43/60) | 0.792 |
| With `parts_merchandise` | 95.0% (57/60) | 0.855 |

Under the initial taxonomy, `inventory` scored 1.00 precision and 0.46 recall: the model did not classify accessories and branded goods as vehicle inventory. Thirteen of seventeen errors were that distinction. Adding a `parts_merchandise` label resolved it; the class now scores 1.00 precision and recall over 17 messages. Accessories and merchandise account for a substantial share of this dealership's traffic, and roughly one message in five requests a human agent.

These figures measure agreement between the model and a single annotator. On 60 examples the 95% confidence interval around 95% is approximately 86-99%. Macro F1 is averaged only over classes present in the annotated set; `financing` (support 1) and `specs` (support 2) are too small to yield a stable per-class score.

## Testing

```bash
python -m pytest tests/ -q
```

39 tests. The suite requires no browser, network access or credentials, and runs in CI on every push.

## Reproducibility

Dependencies are pinned to exact versions. The Scenario III message sample is drawn with a fixed seed and the model is called at `temperature: 0`, so repeated runs over unchanged data produce identical figures.
