"""Configuration and constants for the login worker."""

from __future__ import annotations

import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
LOCAL_INVISIBLE_PLAYWRIGHT_SRC = PROJECT_ROOT / "invisible_playwright" / "src"
if LOCAL_INVISIBLE_PLAYWRIGHT_SRC.exists() and str(LOCAL_INVISIBLE_PLAYWRIGHT_SRC) not in sys.path:
    sys.path.insert(0, str(LOCAL_INVISIBLE_PLAYWRIGHT_SRC))

ACCOUNTS_FILE = PROJECT_ROOT / "accounts.json"
SCREENSHOTS_DIR = PROJECT_ROOT / "screenshots"
SCREENSHOTS_DIR.mkdir(exist_ok=True)


def _int_env(name: str, default: int, minimum: int | None = None) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = default
    if minimum is not None:
        value = max(minimum, value)
    return value


def _bool_env(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _csv_env(name: str, default: tuple[str, ...]) -> set[str]:
    value = os.getenv(name)
    if not value:
        return set(default)
    return {item.strip().lower() for item in value.split(",") if item.strip()}


MAX_RETRIES = _int_env("LOGIN_MAX_RETRIES", 2, minimum=0)
DEVICE_PROMPT_TIMEOUT = _int_env("DEVICE_PROMPT_TIMEOUT", 90, minimum=5)  # seconds
LOGIN_NAVIGATION_TIMEOUT_MS = _int_env("LOGIN_NAVIGATION_TIMEOUT_MS", 75000, minimum=10000)
DEBUG_SCREENSHOTS = _bool_env("DEBUG_SCREENSHOTS", default=False)
BLOCK_HEAVY_RESOURCES = _bool_env("BLOCK_HEAVY_RESOURCES", default=True)
BLOCK_TRACKERS = _bool_env("BLOCK_TRACKERS", default=True)
BLOCKED_RESOURCE_TYPES = _csv_env("BLOCKED_RESOURCE_TYPES", ("image", "media", "font"))
# ── Pixel 10 Pro device fingerprint ────────────────────────────────
# Modern Chrome on Android uses "reduced" User-Agent (Android 10; K)
# with NO device model.  The real identity is conveyed via Client Hints.
_CHROME_MAJOR = "136"  # keep in sync with a recent stable release
ANDROID_USER_AGENT = (
    f"Mozilla/5.0 (Linux; Android 10; K) "
    f"AppleWebKit/537.36 (KHTML, like Gecko) "
    f"Chrome/{_CHROME_MAJOR}.0.0.0 Mobile Safari/537.36"
)
ANDROID_VIEWPORT = {"width": 410, "height": 914}   # real Pixel 10 Pro CSS viewport
ANDROID_DPR = 3.125                                 # real device pixel ratio

# Client Hints that a real Pixel 10 Pro sends.
# Google reads these to determine device eligibility for offers.
CLIENT_HINTS_HEADERS = {
    "sec-ch-ua": f'"Chromium";v="{_CHROME_MAJOR}", "Google Chrome";v="{_CHROME_MAJOR}", "Not-A.Brand";v="99"',
    "sec-ch-ua-mobile": "?1",
    "sec-ch-ua-platform": '"Android"',
    "sec-ch-ua-platform-version": '"16.0.0"',
    "sec-ch-ua-model": '"Pixel 10 Pro"',
    "sec-ch-ua-full-version-list": f'"Chromium";v="{_CHROME_MAJOR}.0.7103.60", "Google Chrome";v="{_CHROME_MAJOR}.0.7103.60", "Not-A.Brand";v="99.0.0.0"',
}

# Google error markers we look for after each step
_WRONG_PASSWORD_MARKERS = [
    "Wrong password",
    "The email and password you entered",
    "Incorrect password",
]
_ACCOUNT_LOCKED_MARKERS = [
    "This account has been disabled",
    "Your account has been suspended",
    "Account disabled",
]
_UNUSUAL_ACTIVITY_MARKERS = [
    "unusual activity",
    "Couldn't sign you in",
    "verify it's you",
    "Confirm your identity",
]
_CAPTCHA_MARKERS = [
    "captcha",
    "recaptcha",
    "g-recaptcha",
]
_DEVICE_PROMPT_MARKERS = [
    "check your phone",
    "tap yes",
    "sent a notification",
    "use your phone",
    "2-step verification",
]
_TRY_ANOTHER_WAY_MARKERS = [
    "try another way",
    "choose another way",
    "more ways to verify",
]
_PAYMENT_REQUIRED_MARKERS = [
    "payment method",
    "add a payment",
    "verify your payment",
    "billing",
    "checkout",
    "review your subscription",
    "subscribe",
]


