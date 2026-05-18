"""Browser launch and stealth helpers."""

from __future__ import annotations

import logging
import random
import re
from contextlib import asynccontextmanager
from typing import Any

from playwright.async_api import ProxySettings, async_playwright

from .config import (
    BLOCK_HEAVY_RESOURCES,
    BLOCK_TRACKERS,
    BLOCKED_RESOURCE_TYPES,
    ANDROID_VIEWPORT,
    ANDROID_DPR,
)

logger = logging.getLogger(__name__)

# ── Pixel 10 Pro identity ────────────────────────────────────────────────────
# Matches device_profiles.py PIXEL_10_PRO exactly.
_DEFAULT_CHROMIUM_VERSION = "136.0.0.0"

# ── Resource blocking ────────────────────────────────────────────────────────

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


def _normalize_chromium_version(version: str) -> str:
    match = re.match(r"^(\d+)(?:\.(\d+))?(?:\.(\d+))?(?:\.(\d+))?", str(version).strip())
    if not match:
        return _DEFAULT_CHROMIUM_VERSION
    return ".".join(part if part is not None else "0" for part in match.groups())


def _chrome_major(version: str) -> str:
    return _normalize_chromium_version(version).split(".", 1)[0]


def _pixel_android_user_agent(chromium_version: str) -> str:
    version = _normalize_chromium_version(chromium_version)
    return (
        "Mozilla/5.0 (Linux; Android 10; K) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        f"Chrome/{version} Mobile Safari/537.36"
    )


def _pixel_client_hints(chromium_version: str) -> dict[str, str]:
    version = _normalize_chromium_version(chromium_version)
    major = _chrome_major(version)
    return {
        "sec-ch-ua": (
            f'"Chromium";v="{major}", '
            f'"Google Chrome";v="{major}", '
            '"Not-A.Brand";v="99"'
        ),
        "sec-ch-ua-mobile": "?1",
        "sec-ch-ua-platform": '"Android"',
        "sec-ch-ua-platform-version": '"16.0.0"',
        "sec-ch-ua-model": '"Pixel 10 Pro"',
        "sec-ch-ua-full-version-list": (
            f'"Chromium";v="{version}", '
            f'"Google Chrome";v="{version}", '
            '"Not-A.Brand";v="99.0.0.0"'
        ),
    }


def _build_playwright_proxy(proxy: dict[str, str] | None) -> ProxySettings | None:
    if not proxy:
        return None

    server = (proxy.get("server") or "").strip()
    if not server or server.lower() == "direct://":
        return None

    pw_proxy: ProxySettings = {"server": server}
    if proxy.get("username"):
        pw_proxy["username"] = proxy["username"]
    if proxy.get("password"):
        pw_proxy["password"] = proxy["password"]
    return pw_proxy


def _build_android_context_kwargs(chromium_version: str) -> dict[str, Any]:
    return {
        "user_agent": _pixel_android_user_agent(chromium_version),
        "viewport": ANDROID_VIEWPORT,
        "device_scale_factor": ANDROID_DPR,
        "is_mobile": True,
        "has_touch": True,
        "locale": "en-US",
        "timezone_id": "Asia/Dhaka",
        "extra_http_headers": _pixel_client_hints(chromium_version),
        "ignore_https_errors": True,
    }


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


# ── Timing helper ────────────────────────────────────────────────────────────

async def _random_pause(page: Any, lo: int = 300, hi: int = 900) -> None:
    """Introduce a human-like pause between actions."""
    await page.wait_for_timeout(random.randint(lo, hi))


# ── Browser launcher ─────────────────────────────────────────────────────────

@asynccontextmanager
async def _launch_android_browser(proxy: dict[str, str] | None):
    """Launch standard Playwright Chromium with Pixel 10 Pro mobile emulation.

    DIAGNOSTIC MODE — using standard Playwright instead of invisible_playwright
    to isolate whether the 'unsafe browser' error is caused by the patched
    Firefox binary or by the proxy / account itself.

    Anti-detection measures applied:
      • --disable-blink-features=AutomationControlled  → navigator.webdriver = false
      • Pixel 10 Pro UA + Client Hints headers
      • Mobile viewport (410×914, DPR 3.125)
      • is_mobile=True, has_touch=True
      • Locale en-US, timezone Asia/Dhaka
      • ignore_https_errors=True  (handles proxy MITM CA)

    TO SWITCH BACK to invisible_playwright, replace this function body with
    the InvisiblePlaywright launcher in the git history.
    """
    pw_proxy = _build_playwright_proxy(proxy)

    # Chromium launch args to minimise automation and headless signals
    launch_args = [
        "--headless=new",                        # new headless = much harder to detect
        "--disable-blink-features=AutomationControlled",
        "--no-sandbox",
        "--disable-dev-shm-usage",
        "--disable-infobars",
        "--disable-extensions",
        "--disable-background-networking",
        "--disable-sync",
        "--disable-translate",
        "--disable-default-apps",
        "--mute-audio",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-popup-blocking",
        "--disable-client-side-phishing-detection",
        "--disable-component-update",
        "--disable-domain-reliability",
        "--disable-features=TranslateUI,BlinkGenPropertyTrees,IsolateOrigins",
        "--lang=en-US",
        # Headless anti-detection
        "--window-size=410,914",
        "--hide-scrollbars",
        "--disable-gpu",
        "--disable-software-rasterizer",
    ]

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=launch_args,
            proxy=pw_proxy,
        )
        try:
            context = await browser.new_context(**_build_android_context_kwargs(browser.version))
            await context.add_init_script("""
                // Remove webdriver flag
                Object.defineProperty(navigator, 'webdriver', {
                    get: () => undefined,
                    configurable: true,
                });
                // Restore window.chrome (missing in headless)
                window.chrome = {
                    runtime: {},
                    loadTimes: function(){},
                    csi: function(){},
                    app: {},
                };
                // Plugins (headless has none — spoof empty array)
                Object.defineProperty(navigator, 'plugins', {
                    get: () => [],
                });
                Object.defineProperty(navigator, 'languages', {
                    get: () => ['en-US', 'en'],
                });
                // Remove headless-specific properties
                delete navigator.__proto__.webdriver;
                // Permissions API — headless returns 'denied' for notifications
                // Make it return 'default' like a real browser
                const originalQuery = window.navigator.permissions.query;
                window.navigator.permissions.query = (parameters) =>
                    parameters.name === 'notifications'
                        ? Promise.resolve({ state: 'default' })
                        : originalQuery(parameters);
            """)
            # Yield a fake "browser" object whose new_context() returns this context
            # We wrap it so runner.py's `browser.new_context()` call works.
            yield _ContextAsNewContextBrowser(browser, context)
        finally:
            await browser.close()


class _ContextAsNewContextBrowser:
    """Thin shim so runner.py's `browser.new_context()` returns our pre-built
    context (with init scripts and mobile settings already applied).

    Runner calls:
        context = await browser.new_context()
        page    = await context.new_page()
    We return our already-configured context on the first call.
    """

    def __init__(self, browser: Any, context: Any) -> None:
        self._browser = browser
        self._context = context
        self._used = False

    async def new_context(self, **_kwargs: Any) -> Any:
        if not self._used:
            self._used = True
            return self._context
        # Subsequent calls (shouldn't happen) fall through to real browser
        return await self._browser.new_context(**_kwargs)
