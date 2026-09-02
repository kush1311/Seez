import os
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
DOWNLOADS_DIR = BASE_DIR / "downloads"
REPORTS_DIR = BASE_DIR / "reports"

for _d in (DOWNLOADS_DIR, REPORTS_DIR):
    _d.mkdir(exist_ok=True, parents=True)

load_dotenv(dotenv_path=BASE_DIR / ".env", override=True)

DASHBOARD_URL = os.getenv("SEEZAR_DASHBOARD_URL", "https://seezar-dashboard.seez.dev")
LOGIN_URL = f"{DASHBOARD_URL}/login"
GRAPHQL_HOST = "platform.seez.dev/api"

SEEZAR_EMAIL = os.getenv("SEEZAR_EMAIL", "").strip()
SEEZAR_PASSWORD = os.getenv("SEEZAR_PASSWORD", "").strip()
GMAIL_USER = os.getenv("GMAIL_USER", "").strip()
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD", "").strip().replace(" ", "")
OTP_SENDER_FILTER = os.getenv("OTP_SENDER_FILTER", "seez.co").strip()
OTP_TIMEOUT_SECONDS = int(os.getenv("OTP_TIMEOUT_SECONDS", "90"))
OTP_MAX_AGE_SECONDS = int(os.getenv("OTP_MAX_AGE_SECONDS", "180"))

STORAGE_STATE_PATH = DOWNLOADS_DIR / "storage_state.json"

HEADLESS = os.getenv("HEADLESS_BROWSER", "false").lower() in ("true", "1", "yes")
NAV_TIMEOUT = 60_000
SETTLE_MS = 11_000
CAPTURE_TIMEOUT_MS = int(os.getenv("CAPTURE_TIMEOUT_MS", "75000"))
CAPTURE_ATTEMPTS = int(os.getenv("CAPTURE_ATTEMPTS", "3"))
# Total budget for the whole export, not for a single click. Generation is
# server-side and slow, and unrelated to size: Croxdale (27 chats) took 603s,
# measured from the click to the button re-enabling. The budget has to outlast a
# build or the archive can never be collected, so it is set above that.
DOWNLOAD_TIMEOUT_MS = int(os.getenv("DOWNLOAD_TIMEOUT_MS", "900000"))
DOWNLOAD_ATTEMPTS = int(os.getenv("DOWNLOAD_ATTEMPTS", "3"))
# How long to wait for the file after a click before concluding that this click
# started a build rather than served one. A warm archive arrives in 2-6s, so 20s is
# generous. The long wait then happens on the button's disabled -> enabled cycle,
# which is the dashboard's own progress signal, instead of on a blind download
# timeout that reports nothing while it runs.
DOWNLOAD_DIRECT_WAIT_MS = int(os.getenv("DOWNLOAD_DIRECT_WAIT_MS", "20000"))
# The export button renders in ~2s when the unit has one at all.
EXPORT_PROBE_MS = int(os.getenv("EXPORT_PROBE_MS", "20000"))

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "").strip()
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "deepseek/deepseek-v4-flash-0731").strip()
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

TOPICS = [
    "pricing", "inventory", "financing", "specs", "test_drive", "trade_in",
    "service", "location", "human_handoff", "parts_merchandise", "other",
]
