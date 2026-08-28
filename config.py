import os
import re
import logging
from pathlib import Path
from dotenv import load_dotenv

logger = logging.getLogger("Config")

# Load .env from project root if it exists
BASE_DIR = Path(__file__).resolve().parent
ENV_PATH = BASE_DIR / ".env"

if ENV_PATH.exists():
    load_dotenv(dotenv_path=ENV_PATH)
else:
    # On Cloud (Render/Koyeb/Docker), environment variables are injected directly
    load_dotenv()

# Telegram Bot configuration
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
if not BOT_TOKEN:
    logger.warning("BOT_TOKEN is not set in environment!")

ADMIN_ID_RAW = os.getenv("ADMIN_IDS", os.getenv("ADMIN_ID", "1452374962,5293338435")).strip()
ADMIN_IDS = set()
for item in re.split(r"[,;\s]+", ADMIN_ID_RAW):
    item = item.strip()
    if item:
        try:
            ADMIN_IDS.add(int(item))
        except ValueError:
            pass

if not ADMIN_IDS:
    ADMIN_IDS = {1452374962, 5293338435}

ADMIN_ID = next(iter(ADMIN_IDS))

# Banner Video configuration
BANNER_PATH_RAW = os.getenv("BANNER_PATH", "banner_video_tiktok/IMG_9654.MOV").strip()
BANNER_PATH = (BASE_DIR / BANNER_PATH_RAW).resolve()

if not BANNER_PATH.exists():
    BANNER_PATH.parent.mkdir(parents=True, exist_ok=True)
    # Create empty placeholder if not present so imports don't fail
    if not BANNER_PATH.exists():
        try:
            BANNER_PATH.touch()
        except Exception:
            pass
    logger.warning(f"Banner video not found at {BANNER_PATH}, created placeholder.")

# Engagement Bait video configuration
BAIT_PATH_RAW = os.getenv("BAIT_PATH", "bait_soxr/bait.mp4").strip()
BAIT_PATH = (BASE_DIR / BAIT_PATH_RAW).resolve()

if not BAIT_PATH.exists():
    BAIT_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not BAIT_PATH.exists():
        try:
            BAIT_PATH.touch()
        except Exception:
            pass
    logger.warning(f"Bait video not found at {BAIT_PATH}, created placeholder.")

# AI API Configurations
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()

# Telethon API Keys
TG_API_ID_RAW = os.getenv("TG_API_ID", "35928731").strip()
try:
    TG_API_ID = int(TG_API_ID_RAW)
except ValueError:
    TG_API_ID = 35928731

TG_API_HASH = os.getenv("TG_API_HASH", "1755f1f9d51c4e97669d0d33c82df09e").strip()

ARCHIVE_CHANNEL_ID = os.getenv("ARCHIVE_CHANNEL_ID", "").strip()
