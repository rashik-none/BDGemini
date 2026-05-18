"""Google login state and challenge helpers."""

from __future__ import annotations

import time
from typing import Any
from urllib.parse import urlencode, urlparse

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from .config import (
    _DEVICE_PROMPT_MARKERS,
    _TRY_ANOTHER_WAY_MARKERS,
    LOGIN_NAVIGATION_TIMEOUT_MS,
)
from .page import _check_markers, _detect_challenge, _page_text


async def _wait_for_navigation(page: Any, timeout: int = 10000) -> None:
    """Wait for a navigation or network-idle, whichever comes first."""
    try:
        await page.wait_for_load_state("networkidle", timeout=timeout)
    except Exception:
        await page.wait_for_timeout(2000)


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


async def _goto_google_login(page: Any, attempts: int = 2) -> None:
    """Open Google's login page with proxy-tolerant navigation handling."""
    url = _google_login_url()
    last_error: Exception | None = None

    for attempt in range(attempts):
        try:
            await page.goto(
                url,
                wait_until="domcontentloaded",
                timeout=LOGIN_NAVIGATION_TIMEOUT_MS,
            )
            return
        except PlaywrightTimeoutError as exc:
            last_error = exc
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
    if not selectors:
        return False
    loc = page.locator(selectors[0])
    for s in selectors[1:]:
        loc = loc.or_(page.locator(s))
    try:
        await loc.first.wait_for(state="visible", timeout=timeout)
        for s in selectors:
            el = page.locator(s).first
            if await el.is_visible():
                await el.click(timeout=3000)
                return True
    except (PlaywrightTimeoutError, PlaywrightError):
        pass
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

