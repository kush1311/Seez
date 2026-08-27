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
DOWNLOAD_TIMEOUT_MS = int(os.getenv("DOWNLOAD_TIMEOUT_MS", "180000"))
DOWNLOAD_ATTEMPTS = int(os.getenv("DOWNLOAD_ATTEMPTS", "2"))

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "").strip()
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "deepseek/deepseek-v4-flash-0731").strip()
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

TOPICS = [
    "pricing", "inventory", "financing", "specs", "test_drive", "trade_in",
    "service", "location", "human_handoff", "other",
]
