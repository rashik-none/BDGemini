"""Browser launch and stealth helpers."""

from __future__ import annotations

import asyncio
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
    ANDROID_SCREEN,
    ANDROID_DPR,
    ANDROID_BUILD_ID,
    ANDROID_DEVICE_MODEL,
    ANDROID_VERSION,
)

logger = logging.getLogger(__name__)

# ── Pixel 10 Pro identity ────────────────────────────────────────────────────
# Uses Pixel 10 Pro geometry/client hints, with model included in UA for
# Google account device-label fallback.
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
        f"Mozilla/5.0 (Linux; Android {ANDROID_VERSION}; "
        f"{ANDROID_DEVICE_MODEL} Build/{ANDROID_BUILD_ID}) "
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
        "screen": ANDROID_SCREEN,
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

    Anti-detection measures applied:
      • --disable-blink-features=AutomationControlled  → navigator.webdriver = false
      • Pixel 10 Pro UA + Client Hints headers (CDP userAgentMetadata override)
      • Mobile viewport (410×914, DPR 3.125)
      • is_mobile=True, has_touch=True
      • Locale en-US, timezone Asia/Dhaka
      • ignore_https_errors=True  (handles proxy MITM CA)
      • Comprehensive init scripts (WebGL, canvas noise, battery, permissions, etc.)
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
            chromium_ver = _normalize_chromium_version(browser.version)
            chrome_major = _chrome_major(chromium_ver)
            ua = _pixel_android_user_agent(chromium_ver)

            context = await browser.new_context(**_build_android_context_kwargs(browser.version))

            # ── CDP: override userAgentMetadata so Chrome itself sends the
            # correct sec-ch-ua-model in every Client Hints request.
            # extra_http_headers alone conflicts with Chrome's internal CH
            # logic — CDP is the authoritative override used by real devices.
            async def _apply_ua_metadata(page: Any) -> None:
                try:
                    cdp = await context.new_cdp_session(page)
                    await cdp.send("Emulation.setUserAgentOverride", {
                        "userAgent": ua,
                        "acceptLanguage": "en-US,en;q=0.9",
                        "platform": "Linux armv8l",
                        "userAgentMetadata": {
                            "brands": [
                                {"brand": "Chromium",     "version": chrome_major},
                                {"brand": "Google Chrome","version": chrome_major},
                                {"brand": "Not-A.Brand",  "version": "99"},
                            ],
                            "fullVersionList": [
                                {"brand": "Chromium",     "version": chromium_ver},
                                {"brand": "Google Chrome","version": chromium_ver},
                                {"brand": "Not-A.Brand",  "version": "99.0.0.0"},
                            ],
                            "fullVersion":   chromium_ver,
                            "platform":      "Android",
                            "platformVersion": "16.0.0",
                            "architecture":  "arm",
                            "model":         "Pixel 10 Pro",
                            "mobile":        True,
                            "bitness":       "64",
                            "wow64":         False,
                        },
                    })
                except Exception:
                    pass  # CDP unavailable — fall back to extra_http_headers

            context.on("page", lambda page: asyncio.ensure_future(_apply_ua_metadata(page)))

            await context.add_init_script("""
                // ── 1. Remove automation traces ───────────────────────────
                Object.defineProperty(navigator, 'webdriver', {
                    get: () => undefined, configurable: true,
                });
                delete navigator.__proto__.webdriver;

                // ── 2. window.chrome (missing in headless) ────────────────
                window.chrome = {
                    runtime: {
                        id: undefined,
                        connect: function(){},
                        sendMessage: function(){},
                    },
                    loadTimes: function(){ return {}; },
                    csi: function(){ return {}; },
                    app: { isInstalled: false, InstallState: {}, RunningState: {} },
                };

                // ── 3. navigator.platform → Android ARM ──────────────────
                Object.defineProperty(navigator, 'platform', {
                    get: () => 'Linux armv8l', configurable: true,
                });

                // ── 4. navigator.vendor ───────────────────────────────────
                Object.defineProperty(navigator, 'vendor', {
                    get: () => 'Google Inc.', configurable: true,
                });

                // ── 5. Hardware / memory ──────────────────────────────────
                Object.defineProperty(navigator, 'hardwareConcurrency', {
                    get: () => 8, configurable: true,
                });
                Object.defineProperty(navigator, 'deviceMemory', {
                    get: () => 8, configurable: true,
                });

                // ── 6. Touch ──────────────────────────────────────────────
                Object.defineProperty(navigator, 'maxTouchPoints', {
                    get: () => 5, configurable: true,
                });

                // ── 7. Languages ──────────────────────────────────────────
                Object.defineProperty(navigator, 'languages', {
                    get: () => ['en-US', 'en'], configurable: true,
                });

                // ── 8. Plugins (empty on Android Chrome) ──────────────────
                Object.defineProperty(navigator, 'plugins', {
                    get: () => [], configurable: true,
                });

                // ── 9. Screen geometry (Pixel 10 Pro logical resolution) ──
                // Physical: 1344×2992 @ 3.25 DPR → logical ≈ 412×919
                // We match the context viewport exactly.
                Object.defineProperty(screen, 'width',      { get: () => 410 });
                Object.defineProperty(screen, 'height',     { get: () => 914 });
                Object.defineProperty(screen, 'availWidth', { get: () => 410 });
                Object.defineProperty(screen, 'availHeight',{ get: () => 914 });
                Object.defineProperty(screen, 'colorDepth', { get: () => 24  });
                Object.defineProperty(screen, 'pixelDepth', { get: () => 24  });

                // ── 10. Connection API → 4G mobile ────────────────────────
                try {
                    const conn = {
                        effectiveType: '4g',
                        rtt: 65,
                        downlink: 18.5,
                        saveData: false,
                        type: 'cellular',
                    };
                    Object.defineProperty(navigator, 'connection', {
                        get: () => conn, configurable: true,
                    });
                } catch(e) {}

                // ── 11. WebGL → Mali-G715 (Pixel 10 Pro GPU) ─────────────
                (function() {
                    const getParameter = WebGLRenderingContext.prototype.getParameter;
                    WebGLRenderingContext.prototype.getParameter = function(param) {
                        if (param === 37445) return 'Google Inc. (ARM)';           // VENDOR
                        if (param === 37446) return 'ANGLE (ARM, Mali-G715, OpenGL ES 3.2)'; // RENDERER
                        return getParameter.call(this, param);
                    };
                    const getParameter2 = WebGL2RenderingContext.prototype.getParameter;
                    WebGL2RenderingContext.prototype.getParameter = function(param) {
                        if (param === 37445) return 'Google Inc. (ARM)';
                        if (param === 37446) return 'ANGLE (ARM, Mali-G715, OpenGL ES 3.2)';
                        return getParameter2.call(this, param);
                    };
                })();

                // ── 12. Canvas noise (mild, per-session random) ───────────
                (function() {
                    const _toDataURL = HTMLCanvasElement.prototype.toDataURL;
                    const _getImageData = CanvasRenderingContext2D.prototype.getImageData;
                    const noise = Math.random() * 0.0003;
                    HTMLCanvasElement.prototype.toDataURL = function(type) {
                        const ctx = this.getContext('2d');
                        if (ctx) {
                            const img = _getImageData.call(ctx, 0, 0, this.width, this.height);
                            for (let i = 0; i < img.data.length; i += 4) {
                                img.data[i]     = Math.min(255, img.data[i]     + (noise * 255 | 0));
                                img.data[i + 1] = Math.min(255, img.data[i + 1] + (noise * 255 | 0));
                                img.data[i + 2] = Math.min(255, img.data[i + 2] + (noise * 255 | 0));
                            }
                            ctx.putImageData(img, 0, 0);
                        }
                        return _toDataURL.apply(this, arguments);
                    };
                })();

                // ── 13. AudioContext fingerprint noise ────────────────────
                (function() {
                    try {
                        const _getChannelData = AudioBuffer.prototype.getChannelData;
                        AudioBuffer.prototype.getChannelData = function() {
                            const data = _getChannelData.apply(this, arguments);
                            for (let i = 0; i < data.length; i += 100) {
                                data[i] += Math.random() * 0.0001 - 0.00005;
                            }
                            return data;
                        };
                    } catch(e) {}
                })();

                // ── 14. Battery API → realistic values ────────────────────
                try {
                    navigator.getBattery = function() {
                        return Promise.resolve({
                            charging: false,
                            chargingTime: Infinity,
                            dischargingTime: 14400,
                            level: 0.72,
                            addEventListener: function(){},
                            removeEventListener: function(){},
                        });
                    };
                } catch(e) {}

                // ── 15. Permissions API → mobile-realistic defaults ───────
                const _origPermQuery = navigator.permissions.query.bind(navigator.permissions);
                navigator.permissions.query = function(desc) {
                    if (desc.name === 'notifications')
                        return Promise.resolve({ state: 'default', onchange: null });
                    if (desc.name === 'push')
                        return Promise.resolve({ state: 'denied', onchange: null });
                    return _origPermQuery(desc);
                };

                // ── 16. window.chrome runtime messaging ───────────────────
                if (!window.chrome.runtime.sendMessage) {
                    window.chrome.runtime.sendMessage = function(){};
                }
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
