import os
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
LOG_DIR = DATA_DIR / "logs"
load_dotenv(BASE_DIR / ".env")

class Config:
    # Google
    GOOGLE_SHEET_ID = os.getenv("GOOGLE_SHEET_ID", "1tEzMN_pDKE-jo2Z2UyPgrTjVA5G_1Fo9OhiHASVuZxE")
    SERVICE_ACCOUNT_FILE = str(BASE_DIR / os.getenv("SERVICE_ACCOUNT_FILE", "credentials/service_account.json"))
    DATABASE_PATH = str(DATA_DIR / "prospect_studio.db")
    SERPAPI_API_KEY = os.getenv("SERPAPI_API_KEY", "")

    # Legacy Apify settings (kept for existing .env files)
    APIFY_API_TOKEN = os.getenv("APIFY_API_TOKEN", "")
    APIFY_ACTOR = os.getenv("APIFY_ACTOR", "apify~google-maps-scraper")

    # OpenRouter (AI)
    OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
    OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "nvidia/nemotron-3.5-lightning:free")

    # Gmail sending (App Password, not OAuth - see README)
    GMAIL_ADDRESS = os.getenv("GMAIL_ADDRESS", "")
    GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD", "")

    # Discord notifications (optional)
    DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "")

    # Signature
    YOUR_NAME = os.getenv("YOUR_NAME", "Ali")
    GITHUB_URL = os.getenv("GITHUB_URL", "https://github.com/alihasnat930")
    UPWORK_URL = os.getenv(
        "UPWORK_URL",
        "https://www.upwork.com/freelancers/~014018226014551443",
    )

config = Config()
