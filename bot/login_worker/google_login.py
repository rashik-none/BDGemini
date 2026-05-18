"""Google login state and challenge helpers."""

from __future__ import annotations

import time
from typing import Any
from urllib.parse import urlencode, urlparse

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from .config import (
    GOOGLE_LOGIN_ATTEMPTS,
    _DEVICE_PROMPT_MARKERS,
    _TRY_ANOTHER_WAY_MARKERS,
    LOGIN_NAVIGATION_TIMEOUT_MS,
    POST_ACTION_SETTLE_MS,
)
from .page import _check_markers, _detect_challenge, _page_text


async def _wait_for_navigation(page: Any, timeout: int = 5000) -> None:
    """Wait briefly for document readiness without relying on network idle."""
    try:
        await page.wait_for_load_state("domcontentloaded", timeout=timeout)
    except Exception:
        pass
    if POST_ACTION_SETTLE_MS:
        await page.wait_for_timeout(POST_ACTION_SETTLE_MS)


def _google_login_url() -> str:
    query = urlencode(
        {
            "flowName": "GlifWebSignIn",
            "flowEntry": "ServiceLogin",
            "continue": "https://one.google.com/",
            "hl": "en",
        }
    )
    return f"https://accounts.google.com/signin/v2/identifier?{query}"


async def _goto_google_login(page: Any, attempts: int = GOOGLE_LOGIN_ATTEMPTS) -> None:
    """Open Google's login page with robust navigation handling.

    Strategy:
      1. Try fast 'commit' (fires as soon as server response bytes arrive).
         Works even when the full page load is slow or SSL is intercepted.
      2. If 'commit' times out, try 'domcontentloaded' as a fallback.
      3. After any successful goto, wait for the email input OR success URL
         so we know the page is actually usable.
    """
    url = _google_login_url()
    last_error: Exception | None = None

    for attempt in range(attempts):
        # ── Phase 1: commit (fastest — fires on first response bytes) ──
        try:
            await page.goto(
                url,
                wait_until="commit",
                timeout=LOGIN_NAVIGATION_TIMEOUT_MS,
            )
            # Give the page a moment to start rendering, then check state
            await page.wait_for_timeout(3000)
            state = await _google_login_state(page)
            if state != "UNKNOWN":
                return
            # Page committed but content not ready yet — wait a bit more
            try:
                await page.wait_for_load_state("domcontentloaded", timeout=10000)
            except Exception:
                pass
            state = await _google_login_state(page)
            if state != "UNKNOWN":
                return
            # Still unknown — raise to trigger outer retry
            raise PlaywrightTimeoutError("Page loaded but Google login UI not found")
        except PlaywrightTimeoutError as exc:
            last_error = exc
            # Check if the page partially loaded anyway
            state = await _google_login_state(page)
            if state != "UNKNOWN":
                return
            if attempt < attempts - 1:
                await page.wait_for_timeout(2000)
                continue
        except PlaywrightError as exc:
            last_error = exc
            if attempt < attempts - 1:
                await page.wait_for_timeout(2000)
                continue

    if last_error:
        raise last_error


def _is_google_login_success_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
    except Exception:
        return False

    host = parsed.netloc.lower()
    if host == "one.google.com" or host.endswith(".one.google.com"):
        return True
    return host in {"myaccount.google.com", "mail.google.com"}


async def _locator_visible(page: Any, selector: str, timeout: int = 300) -> bool:
    try:
        return await page.locator(selector).first.is_visible(timeout=timeout)
    except Exception:
        return False


async def _wait_for_visible_selector(
    page: Any,
    selectors: list[str],
    timeout: int = 20000,
) -> str:
    if not selectors:
        return ""
    loc = page.locator(selectors[0])
    for s in selectors[1:]:
        loc = loc.or_(page.locator(s))
    try:
        await loc.first.wait_for(state="visible", timeout=timeout)
        for s in selectors:
            if await page.locator(s).first.is_visible():
                return s
    except (PlaywrightTimeoutError, PlaywrightError):
        pass
    return ""


async def _click_first_visible(
    page: Any,
    selectors: list[str],
    timeout: int = 5000,
) -> bool:
    """Click the first visible element matching any of the given selectors.

    Uses JS focus()+click() as the primary path to avoid the minimised-Firefox
    viewport geometry bug (scroll-into-view returns 0,0 on a hidden desktop,
    making Playwright declare the element "not visible" even when it is stable).
    Falls back to Playwright's el.click() if JS evaluate fails.
    """
    if not selectors:
        return False

    deadline = time.time() + (timeout / 1000)
    while time.time() < deadline:
        for selector in selectors:
            loc = page.locator(selector)
            try:
                count = min(await loc.count(), 5)
                candidates = [loc.nth(i) for i in range(count)]
            except Exception:
                candidates = [loc.first]

            for el in candidates:
                try:
                    if not await el.is_visible(timeout=300):
                        continue
                    # JS click bypasses scroll-geometry check on minimised window
                    try:
                        await page.evaluate(
                            "(sel) => { const el = document.querySelector(sel); if (el) { el.focus(); el.click(); } }",
                            selector,
                        )
                    except Exception:
                        try:
                            await el.click(timeout=3000)
                        except (PlaywrightTimeoutError, PlaywrightError):
                            await el.evaluate("(node) => node.click()")
                    return True
                except (PlaywrightTimeoutError, PlaywrightError):
                    continue
        await page.wait_for_timeout(300)
    return False


async def _find_totp_selector(page: Any, timeout: int = 8000) -> str:
    return await _wait_for_visible_selector(
        page,
        [
            'input#totpPin',
            'input[type="tel"][name="totpPin"]',
            'input[type="tel"][autocomplete="one-time-code"]',
            'input[type="tel"]',
        ],
        timeout=timeout,
    )


async def _google_login_state(page: Any) -> str:
    if _is_google_login_success_url(page.url):
        return "SUCCESS"

    challenge = await _detect_challenge(page)
    if challenge:
        return challenge

    text = await _page_text(page)
    if await _locator_visible(page, 'input[type="email"]') or await _locator_visible(page, "#identifierId"):
        return "EMAIL"
    if await _locator_visible(page, 'input[type="password"]'):
        return "PASSWORD"
    if await _find_totp_selector(page, timeout=1000):
        return "TOTP"
    if _check_markers(text, _DEVICE_PROMPT_MARKERS):
        return "DEVICE_PROMPT"
    if _check_markers(text, _TRY_ANOTHER_WAY_MARKERS):
        return "TRY_ANOTHER_WAY"
    return "UNKNOWN"


async def _wait_for_google_login_state(
    page: Any,
    wanted: set[str],
    timeout: int = 30000,
) -> str:
    deadline = time.time() + (timeout / 1000)
    terminal = {
        "SUCCESS",
        "WRONG_PASSWORD",
        "ACCOUNT_LOCKED",
        "CAPTCHA",
        "UNUSUAL_ACTIVITY",
    }
    while time.time() < deadline:
        state = await _google_login_state(page)
        if state in wanted or state in terminal:
            return state
        await page.wait_for_timeout(700)
    return await _google_login_state(page)


async def _open_totp_challenge(page: Any) -> None:
    await _click_first_visible(
        page,
        [
            "button:has-text('Try another way')",
            "div[role='button']:has-text('Try another way')",
            "a:has-text('Try another way')",
        ],
        timeout=5000,
    )
    await page.wait_for_timeout(1000)
    await _click_first_visible(
        page,
        [
            "div[role='link']:has-text('Authenticator')",
            "div[role='button']:has-text('Authenticator')",
            "div[role='link']:has-text('verification code')",
            "div[role='button']:has-text('verification code')",
            "text=Get a verification code",
            "text=Google Authenticator",
        ],
        timeout=8000,
    )
    await _wait_for_navigation(page)


async def _open_device_prompt_challenge(page: Any) -> None:
    await _click_first_visible(
        page,
        [
            "button:has-text('Try another way')",
            "div[role='button']:has-text('Try another way')",
            "a:has-text('Try another way')",
        ],
        timeout=5000,
    )
    await page.wait_for_timeout(1000)
    await _click_first_visible(
        page,
        [
            "div[role='link']:has-text('Tap Yes')",
            "div[role='button']:has-text('Tap Yes')",
            "div[role='link']:has-text('phone')",
            "div[role='button']:has-text('phone')",
            "text=Get a prompt",
            "text=Use your phone",
        ],
        timeout=8000,
    )
    await _wait_for_navigation(page)
