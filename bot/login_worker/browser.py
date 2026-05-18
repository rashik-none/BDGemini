"""Browser launch and stealth helpers."""

from __future__ import annotations

import random
from contextlib import asynccontextmanager
from typing import Any

from .config import (
    ANDROID_USER_AGENT,
    BLOCK_HEAVY_RESOURCES,
    BLOCK_TRACKERS,
    BLOCKED_RESOURCE_TYPES,
)

# Import config first so the local invisible_playwright src path is available.
from invisible_playwright.async_api import async_playwright


_STEALTH_JS = """
// Remove webdriver flag
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });

// Spoof plugins (real Chrome has at least a few)
Object.defineProperty(navigator, 'plugins', {
  get: () => [1, 2, 3, 4, 5],
});

// Spoof languages
Object.defineProperty(navigator, 'languages', {
  get: () => ['en-US', 'en'],
});

// Override permissions query to not reveal automation
const originalQuery = window.navigator.permissions.query;
window.navigator.permissions.query = (parameters) => (
  parameters.name === 'notifications'
    ? Promise.resolve({ state: Notification.permission })
    : originalQuery(parameters)
);

// Hide headless chrome signals
if (window.chrome === undefined) {
  window.chrome = { runtime: {}, loadTimes: function(){}, csi: function(){} };
}
"""

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


async def _add_stealth(context: Any) -> None:
    """Inject stealth patches into every new page."""
    await context.add_init_script(_STEALTH_JS)


def _should_block_request(url: str, resource_type: str) -> bool:
    lowered_url = str(url).lower()
    lowered_resource_type = str(resource_type).lower()

    if BLOCK_HEAVY_RESOURCES and lowered_resource_type in BLOCKED_RESOURCE_TYPES:
        return True

    if BLOCK_TRACKERS and any(marker in lowered_url for marker in _TRACKER_URL_MARKERS):
        return True

    return False


async def _block_heavy_resources(context: Any) -> None:
    """Block heavy or tracking requests to reduce metered proxy usage."""
    if not BLOCK_HEAVY_RESOURCES and not BLOCK_TRACKERS:
        return

    async def route_handler(route: Any) -> None:
        request = route.request
        if _should_block_request(request.url, request.resource_type):
            await route.abort()
            return
        await route.continue_()

    await context.route("**/*", route_handler)


async def _random_pause(page: Any, lo: int = 300, hi: int = 900) -> None:
    """Introduce a human-like pause between actions."""
    await page.wait_for_timeout(random.randint(lo, hi))


@asynccontextmanager
async def _launch_android_browser(proxy: dict[str, str] | None):
    """Launch Chromium because Firefox/invisible_playwright cannot use is_mobile."""
    playwright = await async_playwright().start()
    browser = None
    try:
        launch_kwargs: dict[str, Any] = {
            "headless": True,
            # Bypass SSL cert errors from proxy MITM interception
            # (some proxy providers replace TLS certs with their own)
            "ignore_https_errors": True,
            "args": [
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
                "--no-sandbox",
                "--disable-infobars",
                "--disable-background-networking",
                "--disable-component-update",
                "--disable-default-apps",
                "--disable-extensions",
                # Ignore TLS/SSL certificate errors (needed for proxy MITM)
                "--ignore-certificate-errors",
                "--ignore-ssl-errors",
                f"--user-agent={ANDROID_USER_AGENT}",
            ],
        }
        if proxy:
            launch_kwargs["proxy"] = proxy

        last_error: Exception | None = None
        for channel in (None, "chrome", "msedge"):
            try:
                candidate_kwargs = dict(launch_kwargs)
                if channel:
                    candidate_kwargs["channel"] = channel
                browser = await playwright.chromium.launch(**candidate_kwargs)
                break
            except Exception as exc:
                last_error = exc

        if browser is None:
            raise RuntimeError(
                "Android mobile mode requires Chromium, Chrome, or Edge. "
                "Install Playwright Chromium with: python -m playwright install chromium"
            ) from last_error

        yield browser
    finally:
        if browser is not None:
            try:
                await browser.close()
            except Exception:
                pass
        await playwright.stop()
