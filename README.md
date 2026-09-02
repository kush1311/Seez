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

`app.py` drops `seezar_operator` from `sys.modules` on each rerun, so edits to the operator take effect without restarting the server. Streamlit would otherwise hold the originally imported modules for the life of the process.

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
tests/                       50 tests
```

### Data acquisition

The dashboard renders its analytics charts to `<canvas>`, so the underlying values are not present in the DOM. The operator navigates the interface with Playwright and concurrently records the GraphQL responses the application issues, reading values from the `botMetrics` payload. Conversation content is obtained the same way, from `seezarChats` and `getUserChatHistory` as each chat is opened.

### Identifier resolution

Dealership identifiers, bot identifiers and the dealership list are resolved at runtime from the `getDealerships` query. Dealership names supplied on the command line are matched case-insensitively, with ambiguous matches rejected.

### Bot selection

A dealership may host several bots, such as an internal support assistant alongside a customer-facing one. The operator reads each bot's `botType` from `queryDealershipById` and selects the customer-facing bot. Analytics may be served for a different bot and redirect to it, so each report records the bot its figures describe.

### Metric derivation

The language model assigns a topic label to each message and extracts any vehicle names it contains. All counts, shares and ratios are computed from those labels in Python.

## Scenario II: messages per chat

The operator downloads the Chat History archive, parses the CSV files it contains, and divides the total row count by the number of distinct `Chat Ref` values.

Three properties of the archive affect parsing, all covered by `tests/test_chat_data.py`:

- It is UTF-8 with a byte-order mark, so it must be read as `utf-8-sig` or the first column name is corrupted.
- Message text contains embedded newlines, so the file has more physical lines than data rows. Row counts come from a CSV parse rather than a line count.
- A dealership group ships one file per franchise. Every CSV in the archive is read, not just the first.

The archive covers a dealership's full history, whereas the dashboard's own messages-per-chat figure is scoped to the selected date range. The two are reported separately.

## Scenario III: deep-dive explorer

The operator reads the analytics vehicle figures, then opens the Conversations tab and reads each chat. Every conversation figure comes from that tab; the Chat History archive belongs to Scenario II and is not used here.

The chat list renders 20 rows per page while the underlying query returns every chat, so the operator pages through the list to reach chats beyond the first page. `--max-chats` bounds how many are read, and the number listed against the number read is stated in the report.

The report covers how many of the chats read mention the most-clicked model, which models customers named, and the topic distribution over all messages read and over the subset naming a vehicle. Inconsistencies between the analytics payload and the conversation data are reported rather than reconciled.

Classification uses a language model because the conversations mix Danish and English: `"Kan jeg bytte min gamle bil ind?"` is a trade-in enquiry, and `"Hvad koster den om maneden med udbetaling?"` concerns financing rather than pricing. Neither is reachable by English keyword matching.

The brief describes clicking the most-clicked model. That element is an `H2` heading with `cursor: auto` and no click handler, so the value is read from the analytics payload instead.

## Evaluation

`eval/gold_labels.json` contains messages drawn from the live export and annotated by hand.

```bash
python evaluate.py --save
```

Classifies the annotated set and reports accuracy, macro F1, per-class precision and recall, and every disagreement between the annotation and the model. Macro F1 is averaged only over classes present in the annotated set. The figures measure agreement with a single annotator rather than objective truth, and the set is small enough that per-class scores for rare labels are unstable.

## Testing

```bash
python -m pytest tests/ -q
```

The suite requires no browser, network access or credentials, and runs in CI on every push.

## Reproducibility

Dependencies are pinned to exact versions. Scenario III reads chats in the order the Conversations tab lists them, so the input to classification is stable, and the model is called at `temperature: 0`.

Figures read from the dashboard - row counts, chat counts, mention counts and the messages-per-chat ratio - are exact and repeat identically. Topic shares do not: the provider does not guarantee identical output for identical input, and accuracy on the annotated set has varied by several points across runs on the same data. Treat topic distributions as approximate. Where a batch returns fewer labels than messages sent, the missing ones are re-requested and any that remain unanswered are logged and counted as `other`.
