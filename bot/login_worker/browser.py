"""Browser launch and stealth helpers."""

from __future__ import annotations

import logging
import random
import sys
from contextlib import asynccontextmanager
from typing import Any

from .config import (
    ANDROID_USER_AGENT,
    BLOCK_HEAVY_RESOURCES,
    BLOCK_TRACKERS,
    BLOCKED_RESOURCE_TYPES,
)

# config.py already inserts invisible_playwright/src into sys.path
from invisible_playwright.async_api import InvisiblePlaywright

logger = logging.getLogger(__name__)

# ── Resource blocking ───────────────────────────────────────────────────────

_TRACKER_URL_MARKERS = (
    "google-analytics",
    "googletagmanager",
    "doubleclick",
    "analytics",
    "ads",
    "adservice",
    "tracking",
    "telemetry",
)

# These Google domains must NEVER have their resources blocked — blocking
# scripts/stylesheets here breaks the login UI or offer-claim flow.
_GOOGLE_CRITICAL_HOSTS = (
    "accounts.google.com",
    "one.google.com",
    "myaccount.google.com",
    "store.google.com",
    "apis.google.com",
    "gstatic.com",
    "google.com/recaptcha",
)


def _should_block_request(url: str, resource_type: str) -> bool:
    lowered_url = url.lower()
    lowered_resource_type = resource_type.lower()

    # Never block anything from critical Google domains
    if any(host in lowered_url for host in _GOOGLE_CRITICAL_HOSTS):
        return False

    if BLOCK_HEAVY_RESOURCES and lowered_resource_type in BLOCKED_RESOURCE_TYPES:
        return True

    if BLOCK_TRACKERS and any(marker in lowered_url for marker in _TRACKER_URL_MARKERS):
        return True

    return False


async def _block_heavy_resources(context: Any) -> None:
    """Block heavy/tracking requests while protecting Google critical paths."""
    if not BLOCK_HEAVY_RESOURCES and not BLOCK_TRACKERS:
        return

    async def route_handler(route: Any) -> None:
        try:
            request = route.request
            if _should_block_request(request.url, request.resource_type):
                await route.abort()
            else:
                await route.continue_()
        except Exception:
            # Route may already be handled (page navigated away). Ignore.
            pass

    await context.route("**/*", route_handler)


# ── Timing helper ───────────────────────────────────────────────────────────

async def _random_pause(page: Any, lo: int = 300, hi: int = 900) -> None:
    """Introduce a human-like pause between actions."""
    await page.wait_for_timeout(random.randint(lo, hi))


# ── Browser launcher ────────────────────────────────────────────────────────

@asynccontextmanager
async def _launch_android_browser(proxy: dict[str, str] | None):
    """Launch InvisiblePlaywright (patched Firefox) for maximum stealth.

    WHY FIREFOX, NOT CHROME?
    ─────────────────────────
    Regular Playwright Chromium is trivially detected by Google:
      • navigator.webdriver = true (even with flags)
      • headless-specific timing, GPU & rendering signatures
      • No real plugin/codec fingerprint

    invisible_playwright patches Firefox at the C++ level (Gecko source),
    not via JS overrides. reCAPTCHA v3 score: 0.90/1.0 vs ~0.3-0.5
    for patched Chromium. Google classifies the session as "very likely human".

    HEADLESS ON WINDOWS
    ────────────────────
    invisible_playwright uses _WindowsVirtualDesktop (CreateDesktop via
    pywin32/ctypes) to run Firefox headed on a hidden desktop — it avoids
    the divergent code paths that headless=True triggers inside Gecko,
    which are fingerprinted by anti-bot systems.
    Requires: pywin32  (pip install pywin32)

    MOBILE SPOOFING
    ────────────────
    Firefox does NOT support Playwright's is_mobile / has_touch context
    options. We spoof mobile identity via:
      • general.useragent.override pref  (UA string)
      • sec-ch-ua / sec-ch-ua-mobile headers  (Client Hints)
      • viewport + device_scale_factor  (screen size)
    This is sufficient for Google's login and offer-claim flows.
    """
    extra_prefs: dict[str, Any] = {
        # ── SSL / Proxy MITM bypass ───────────────────────────────────────
        # Bright Data (and most residential proxies) perform HTTPS inspection
        # by substituting their own CA cert. These prefs allow that.
        "network.stricttransportsecurity.preloadlist": False,
        "security.cert_pinning.enforcement_level": 0,
        "security.mixed_content.block_active_content": False,
        "security.mixed_content.block_display_content": False,
        # Trust the OS/system root certificates (catches proxy CAs installed
        # at the Windows certificate store level by Bright Data client)
        "security.enterprise_roots.enabled": True,
        # ── Mobile UA spoof ───────────────────────────────────────────────
        "general.useragent.override": ANDROID_USER_AGENT,
        # ── Performance / stability ───────────────────────────────────────
        # Disable telemetry pings that waste proxy bandwidth
        "datareporting.healthreport.uploadEnabled": False,
        "datareporting.policy.dataSubmissionEnabled": False,
        "toolkit.telemetry.enabled": False,
        "toolkit.telemetry.unified": False,
        # Disable auto-update checks during session
        "app.update.auto": False,
        "app.update.enabled": False,
        # Disable safe-browsing pings (proxy bandwidth)
        "browser.safebrowsing.malware.enabled": False,
        "browser.safebrowsing.phishing.enabled": False,
    }

    try:
        is_windows = sys.platform == "win32"
        ipl = InvisiblePlaywright(
            proxy=proxy,
            # Windows: SetThreadDesktop fails in asyncio thread, so we launch minimized.
            # Linux (VPS): Xvfb works perfectly, so we use true headless mode.
            headless=not is_windows,
            extra_args=["-min"] if is_windows else [],
            locale="en-US",
            timezone="Asia/Dhaka",
            extra_prefs=extra_prefs,
            humanize=True,         # Bezier-curve mouse motion baked in binary
        )
        async with ipl as browser:
            yield browser
    except Exception:
        raise
