"""Configuration and constants for the login worker."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from bot.utils import int_env as _int_env

PROJECT_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class DeviceProfile:
    viewport: dict[str, int]
    screen: dict[str, int]
    device_scale_factor: float
    extra_http_headers: dict[str, str]


PIXEL_10_PRO = DeviceProfile(
    viewport={"width": 410, "height": 914},
    screen={"width": 410, "height": 914},
    device_scale_factor=3.125,
    extra_http_headers={
        "sec-ch-ua": '"Chromium";v="136", "Google Chrome";v="136", "Not-A.Brand";v="99"',
        "sec-ch-ua-mobile": "?1",
        "sec-ch-ua-platform": '"Android"',
        "sec-ch-ua-platform-version": '"16.0.0"',
        "sec-ch-ua-model": '"Pixel 10 Pro"',
        "sec-ch-ua-full-version-list": (
            '"Chromium";v="136.0.0.0", '
            '"Google Chrome";v="136.0.0.0", '
            '"Not-A.Brand";v="99.0.0.0"'
        ),
    },
)

ACCOUNTS_FILE = PROJECT_ROOT / "accounts.json"
SCREENSHOTS_DIR = PROJECT_ROOT / "screenshots"
SCREENSHOTS_DIR.mkdir(exist_ok=True)



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
# Keep Pixel 10 Pro in both Client Hints and UA fallback. Google account
# security prompts may show generic Android if the UA has no model.
#
# Tensor G5 SoC → GPU: Imagination PowerVR DXT-48-1536
#   ANGLE WebGL string: "ANGLE (Imagination Technologies, PowerVR D-Series DXT-48-1536, OpenGL ES 3.2)"
#   (browser.py init script must match this — NOT Mali-G715 which is Tensor G4)
PIXEL_10_PRO_PROFILE = PIXEL_10_PRO
ANDROID_DEVICE_MODEL = "Google Pixel 10 Pro"
ANDROID_VERSION = "16"
ANDROID_BUILD_ID = "CP1A.260505.005"
ANDROID_CHROME_VERSION = "136.0.0.0"
ANDROID_USER_AGENT: str = (
    f"Mozilla/5.0 (Linux; Android {ANDROID_VERSION}; "
    f"{ANDROID_DEVICE_MODEL} Build/{ANDROID_BUILD_ID}) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    f"Chrome/{ANDROID_CHROME_VERSION} Mobile Safari/537.36"
)
ANDROID_VIEWPORT: dict[str, int] = dict(PIXEL_10_PRO.viewport)
ANDROID_SCREEN: dict[str, int] = dict(PIXEL_10_PRO.screen)
ANDROID_DPR: float = PIXEL_10_PRO.device_scale_factor

# Client Hints that a real Pixel 10 Pro sends.
# Google reads these to determine device eligibility for offers.
CLIENT_HINTS_HEADERS: dict[str, str] = dict(PIXEL_10_PRO.extra_http_headers)

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
# Google shows this interstitial when a new device/browser is detected.
# It contains a "Yes, it's me" / "Continue" button to confirm the login.
_NEW_DEVICE_CONFIRM_MARKERS = [
    "signed in on android",
    "signed in on pixel",
    "new device sign-in",
    "new sign-in",
    "check that it was you",
    "it's really you",
    "yes, it's me",
    "confirm it's you",
    "recognize this activity",
    "was this you",
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
