"""Bot-level configuration loaded from environment / api.env."""

import os
from pathlib import Path

from dotenv import load_dotenv

# Load env from project root
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_PROJECT_ROOT / "api.env")

# ── Telegram ─────────────────────────────────────────────────────────
BOT_TOKEN = os.getenv("8957506094:AAFXQfsH88FGPUfQi-Sy3oE9qU5tI7PnX2k", "8957506094:AAFXQfsH88FGPUfQi-Sy3oE9qU5tI7PnX2k")
CHAT_ID = os.getenv("CHAT_ID", "")
_admin_ids_raw = os.getenv("ADMIN_USER_IDS", "").strip() or "6196481482"
ADMIN_USER_IDS = {item.strip() for item in _admin_ids_raw.split(",") if item.strip()}

# ── UI ───────────────────────────────────────────────────────────────
BOT_TITLE = os.getenv("BOT_TITLE", "BDGeminBot")
BOT_USERNAME = os.getenv("BOT_USERNAME", "BDGeminBot")
DEFAULT_NAME = os.getenv("USER_NAME")

# ── Economy ──────────────────────────────────────────────────────────
REFERRAL_USERS_PER_CREDIT = int(os.getenv("REFERRAL_USERS_PER_CREDIT", "10"))
VERIFY_PRICE = int(os.getenv("VERIFY_PRICE", "1"))

# ── Paths ────────────────────────────────────────────────────────────
ACCOUNTS_FILE = _PROJECT_ROOT / "accounts.json"
SCREENSHOTS_DIR = _PROJECT_ROOT / "screenshots"

# Ensure directories exist
SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)

# int_env is available from bot.utils for callers that need the minimum clamp.
