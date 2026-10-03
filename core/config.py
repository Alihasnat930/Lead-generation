import os
import hashlib
import hmac
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
LOG_DIR = DATA_DIR / "logs"
load_dotenv(BASE_DIR / ".env")

class Config:
    CLOUD_MODE = os.getenv("APP_ENV", "local").lower() == "production"
    APP_LOGIN_PASSWORD_HASH = os.getenv("APP_LOGIN_PASSWORD_HASH", "")
    SUPABASE_URL = os.getenv("SUPABASE_URL", "")
    SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
    # Google
    GOOGLE_SHEET_ID = os.getenv("GOOGLE_SHEET_ID", "")
    SERVICE_ACCOUNT_FILE = str(BASE_DIR / os.getenv("SERVICE_ACCOUNT_FILE", "credentials/service_account.json"))
    SERVICE_ACCOUNT_JSON = os.getenv("SERVICE_ACCOUNT_JSON", "")
    DATABASE_PATH = str(DATA_DIR / "prospect_studio.db")
    OUTREACH_DATABASE_PATH = str(DATA_DIR / "outreach.db")
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
    LINKEDIN_URL = os.getenv("LINKEDIN_URL", "https://www.linkedin.com/in/syed-ali-hasnat-danyal-6b0a26246/")
    GITHUB_URL = os.getenv("GITHUB_URL", "https://github.com/alihasnat930")
    UPWORK_URL = os.getenv(
        "UPWORK_URL",
        "https://www.upwork.com/freelancers/~014018226014551443",
    )

config = Config()


def verify_app_password(password):
    """Verify PBKDF2 hashes; retain compatibility with existing SHA-256 settings."""
    if not config.APP_LOGIN_PASSWORD_HASH:
        return False
    stored = config.APP_LOGIN_PASSWORD_HASH.strip()
    if stored.startswith('pbkdf2_sha256$'):
        try:
            _, rounds, salt, digest = stored.split('$')
            iterations = int(rounds)
            if not 100000 <= iterations <= 2000000:
                return False
            candidate = hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(salt), iterations).hex()
            return hmac.compare_digest(candidate, digest)
        except (ValueError, TypeError):
            return False
    candidate = hashlib.sha256(password.encode("utf-8")).hexdigest()
    return hmac.compare_digest(candidate, stored.lower())


def hash_app_password(password):
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac('sha256', password.encode(), salt, 600000).hex()
    return f'pbkdf2_sha256$600000${salt.hex()}${digest}'
