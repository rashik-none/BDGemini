"""Shared page and text helpers."""

from __future__ import annotations

import logging
import random
import re
import time
from typing import Any
from urllib.parse import urlparse

from playwright.async_api import Error as PlaywrightError

from bot.utils import mask_email as _mask_email  # consolidated; re-exported for login_worker

from .humanize import _dwell_before_action, _simulate_touch

from .config import (
    _ACCOUNT_LOCKED_MARKERS,
    _CAPTCHA_MARKERS,
    _UNSAFE_BROWSER_MARKERS,
    _UNUSUAL_ACTIVITY_MARKERS,
    _WRONG_PASSWORD_MARKERS,
    DEBUG_SCREENSHOTS,
    SCREENSHOTS_DIR,
)

logger = logging.getLogger(__name__)

_SAFE_FILENAME_RE = re.compile(r"[^a-zA-Z0-9_.-]+")


def _safe_filename_part(value: str, fallback: str) -> str:
    cleaned = _SAFE_FILENAME_RE.sub("_", value).strip("._")
    return cleaned or fallback


async def _screenshot(page: Any, job_id: str, step: str) -> str:
    """Save a timestamped screenshot and return its path."""
    if not DEBUG_SCREENSHOTS:
        return ""

    ts = time.strftime("%Y%m%d_%H%M%S")
    safe_job_id = _safe_filename_part(job_id, "job")
    safe_step = _safe_filename_part(step, "step")
    path = SCREENSHOTS_DIR / f"{safe_job_id}_{safe_step}_{ts}_{time.time_ns()}.png"
    try:
        await _mask_sensitive_inputs(page)
        await page.screenshot(path=str(path), full_page=True)
    except Exception:
        logger.warning("[%s] Screenshot failed at step '%s'", job_id, step)
        return ""
    return str(path)


async def _page_text(page: Any) -> str:
    """Get visible body text (lowercase) for marker checks."""
    try:
        return str(await page.inner_text("body")).lower()
    except Exception:
        return ""


def _check_markers(text: str, markers: list[str]) -> bool:
    lowered = text.lower()
    return any(m.lower() in lowered for m in markers)


async def _mask_sensitive_inputs(page: Any) -> None:
    """Visually hide credential fields before optional debug screenshots."""
    try:
        await page.evaluate(
            """
            () => {
              const selectors = [
                'input[type="email"]',
                'input[type="password"]',
                'input[type="tel"]',
                'input[autocomplete*="one-time-code" i]',
                'input[name*="email" i]',
                'input[name*="identifier" i]',
                'input[name*="passwd" i]',
                'input[name*="password" i]',
                'input[name*="totp" i]',
                'input[name*="otp" i]',
                'input[id*="totp" i]',
                'input[id*="otp" i]',
                'textarea[name*="secret" i]'
              ];
              for (const el of document.querySelectorAll(selectors.join(','))) {
                el.style.setProperty('-webkit-text-security', 'disc', 'important');
                el.style.setProperty('color', 'transparent', 'important');
                el.style.setProperty('text-shadow', '0 0 0 #111', 'important');
                el.style.setProperty('caret-color', 'transparent', 'important');
              }
            }
            """
        )
    except Exception:
        logger.debug("Sensitive input masking skipped", exc_info=True)


def _safe_proxy_label(proxy: dict[str, str] | None) -> str:
    if not proxy:
        return "direct"

    server = proxy.get("server", "").strip()
    if not server:
        return "configured proxy"

    parsed = urlparse(server)
    if parsed.scheme and parsed.hostname:
        label = f"{parsed.scheme}://{parsed.hostname}"
        try:
            port = parsed.port
        except ValueError:
            port = None
        if port:
            label += f":{port}"
        return label

    if "@" in server:
        server = server.rsplit("@", 1)[1]
    return server


def _redact_sensitive(text: str, *values: str) -> str:
    redacted = text
    for value in values:
        if value:
            redacted = redacted.replace(value, "[redacted]")
    return redacted


async def _detect_challenge(page: Any) -> str | None:
    """Return a challenge type string if a known blocker is visible."""
    text = await _page_text(page)
    if _check_markers(text, _WRONG_PASSWORD_MARKERS):
        return "WRONG_PASSWORD"
    if _check_markers(text, _ACCOUNT_LOCKED_MARKERS):
        return "ACCOUNT_LOCKED"
    if _check_markers(text, _UNSAFE_BROWSER_MARKERS):
        return "UNSAFE_BROWSER"
    if _check_markers(text, _CAPTCHA_MARKERS):
        return "CAPTCHA"
    if _check_markers(text, _UNUSUAL_ACTIVITY_MARKERS):
        return "UNUSUAL_ACTIVITY"
    return None


async def _human_type(page: Any, selector: str, text: str) -> None:
    """Type text character-by-character with random delays for realism.

    WHY JS focus() instead of el.click():
    ───────────────────────────────────────
    Playwright's click() internally scrolls the element into the viewport and
    then re-checks its bounding box. On a minimised/off-screen window the
    geometry check fails with "element is not visible" even though the element
    is perfectly stable.

    Using evaluate() → el.focus() + el.click() bypasses the scroll-geometry
    check entirely and focuses the input directly, which is all we need before
    fill() / type().
    """
    el = page.locator(selector).first
    await el.wait_for(state="visible", timeout=10000)
    # Pre-focus dwell — real users pause before tapping an input field
    await _dwell_before_action(page)
    # Touch simulation — real mobile users touch the field before typing
    await _simulate_touch(page, selector)
    # Focus via JS to avoid scroll-geometry failure on minimised window
    try:
        await page.evaluate(
            "(sel) => { const el = document.querySelector(sel); if (el) { el.focus(); el.click(); } }",
            selector,
        )
    except Exception:
        # Last-resort fallback: try Playwright click (may fail on minimised window)
        try:
            await el.click(timeout=5000)
        except Exception:
            pass
    # Brief pause between focus and first keystroke (perception-reaction delay)
    await page.wait_for_timeout(random.randint(150, 500))
    await el.fill("", timeout=10000)
    await el.type(text, delay=random.randint(40, 120))
    try:
        actual = await el.input_value(timeout=5000)
    except Exception:
        actual = ""
    if actual != text:
        await el.fill(text, timeout=10000)
        try:
            actual = await el.input_value(timeout=5000)
        except Exception:
            actual = ""
    if actual != text:
        raise PlaywrightError("Input value did not match expected text after typing")
