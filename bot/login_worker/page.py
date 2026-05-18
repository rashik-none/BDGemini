"""Shared page and text helpers."""

from __future__ import annotations

import logging
import random
import re
import time
from typing import Any
from urllib.parse import urlparse

from .config import (
    _ACCOUNT_LOCKED_MARKERS,
    _CAPTCHA_MARKERS,
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


def _mask_email(email: str) -> str:
    email = email.strip()
    if "@" not in email:
        return email
    local, domain = email.split("@", 1)
    if not local:
        return f"*@{domain}"
    if len(local) <= 2:
        masked_local = local[0] + "*"
    else:
        masked_local = f"{local[0]}{'*' * min(len(local) - 2, 5)}{local[-1]}"
    return f"{masked_local}@{domain}"


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
    redacted = str(text)
    for value in values:
        if value:
            redacted = redacted.replace(str(value), "[redacted]")
    return redacted


async def _detect_challenge(page: Any) -> str | None:
    """Return a challenge type string if a known blocker is visible."""
    text = await _page_text(page)
    if _check_markers(text, _WRONG_PASSWORD_MARKERS):
        return "WRONG_PASSWORD"
    if _check_markers(text, _ACCOUNT_LOCKED_MARKERS):
        return "ACCOUNT_LOCKED"
    if _check_markers(text, _CAPTCHA_MARKERS):
        return "CAPTCHA"
    if _check_markers(text, _UNUSUAL_ACTIVITY_MARKERS):
        return "UNUSUAL_ACTIVITY"
    return None


async def _human_type(page: Any, selector: str, text: str) -> None:
    """Type text character-by-character with random delays for realism."""
    el = page.locator(selector)
    await el.click()
    await el.fill("")
    await el.type(text, delay=random.randint(40, 120))
