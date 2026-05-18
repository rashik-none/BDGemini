"""Configuration and constants for the login worker."""

from __future__ import annotations

import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
LOCAL_INVISIBLE_PLAYWRIGHT_SRC = PROJECT_ROOT / "invisible_playwright" / "src"
if LOCAL_INVISIBLE_PLAYWRIGHT_SRC.exists() and str(LOCAL_INVISIBLE_PLAYWRIGHT_SRC) not in sys.path:
    sys.path.insert(0, str(LOCAL_INVISIBLE_PLAYWRIGHT_SRC))

from invisible_playwright.device_profiles import get_device_profile

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
GOOGLE_LOGIN_ATTEMPTS = _int_env("GOOGLE_LOGIN_ATTEMPTS", 2, minimum=1)
DEVICE_PROMPT_TIMEOUT = _int_env("DEVICE_PROMPT_TIMEOUT", 90, minimum=5)  # seconds
LOGIN_NAVIGATION_TIMEOUT_MS = _int_env("LOGIN_NAVIGATION_TIMEOUT_MS", 45000, minimum=10000)
POST_ACTION_SETTLE_MS = _int_env("POST_ACTION_SETTLE_MS", 800, minimum=0)
DEBUG_SCREENSHOTS = _bool_env("DEBUG_SCREENSHOTS", default=False)
BLOCK_HEAVY_RESOURCES = _bool_env("BLOCK_HEAVY_RESOURCES", default=True)
BLOCK_TRACKERS = _bool_env("BLOCK_TRACKERS", default=True)
BLOCKED_RESOURCE_TYPES = _csv_env("BLOCKED_RESOURCE_TYPES", ("image", "media", "font"))
# ── Pixel 10 Pro device fingerprint ────────────────────────────────
# Modern Chrome on Android uses "reduced" User-Agent (Android 10; K)
# with NO device model.  The real identity is conveyed via Client Hints.
PIXEL_10_PRO_PROFILE = get_device_profile("pixel_10_pro")
ANDROID_USER_AGENT = PIXEL_10_PRO_PROFILE.user_agent
ANDROID_VIEWPORT = dict(PIXEL_10_PRO_PROFILE.viewport)
ANDROID_DPR = PIXEL_10_PRO_PROFILE.device_scale_factor

# Client Hints that a real Pixel 10 Pro sends.
# Google reads these to determine device eligibility for offers.
CLIENT_HINTS_HEADERS = dict(PIXEL_10_PRO_PROFILE.extra_http_headers)

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
_UNSAFE_BROWSER_MARKERS = [
    # Google shows all three on the same error page:
    # title: "Couldn't sign you in"
    # body:  "This browser or app may not be secure. Try using a different browser."
    "Couldn't sign you in",
    "This browser or app may not be secure",
    "browser or app may not be secure",
    "Try using a different browser",
    "this browser or app",
]
_UNUSUAL_ACTIVITY_MARKERS = [
    "unusual activity",
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


