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
    _NEW_DEVICE_CONFIRM_MARKERS,
    _TRY_ANOTHER_WAY_MARKERS,
    LOGIN_NAVIGATION_TIMEOUT_MS,
    POST_ACTION_SETTLE_MS,
)
from .page import _check_markers, _detect_challenge, _page_text

GOOGLE_EMAIL_SELECTORS = [
    'input[type="email"]',
    'input[name="identifier"]',
    'input#identifierId',
    "#identifierId",
    'input[autocomplete="username"]',
    'input[aria-label="Email or phone"]',
    'input[type="text"][autocomplete="username"]',
]

GOOGLE_PASSWORD_SELECTORS = [
    'input[type="password"]',
    'input[name="Passwd"]',
    'input[autocomplete="current-password"]',
]


async def _wait_for_navigation(page: Any, timeout: int = 5000) -> None:
    """Wait briefly for document readiness without relying on network idle."""
    try:
        await page.wait_for_load_state("domcontentloaded", timeout=timeout)
    except Exception:
        pass
    if POST_ACTION_SETTLE_MS:
        await page.wait_for_timeout(POST_ACTION_SETTLE_MS)


def _google_login_url() -> str:
    # Navigate to accounts.google.com directly.
    #
    # WHY NOT one.google.com?
    # ────────────────────────────────────────────────────────────
    # When the bot goes to one.google.com while unauthenticated, Google may
    # serve the anonymous pricing/plans page ("Choose the Google One plan")
    # WITHOUT always redirecting to accounts.google.com — especially when
    # using a proxy or a fresh cookie jar that Google doesn't trust yet.
    # That anonymous page was being incorrectly classified as a login SUCCESS
    # because one.google.com was listed in _is_google_login_success_url().
    #
    # Going straight to accounts.google.com/signin guarantees we always land
    # on the email-input form. The continue URL makes Google complete the
    # service sign-in against Google One instead of stopping on My Account.
    query = urlencode(
        {
            "continue": "https://one.google.com/",
            "hl": "en",
            "flowName": "GlifWebSignIn",
            "flowEntry": "ServiceLogin",
        }
    )
    return f"https://accounts.google.com/signin/v2/identifier?{query}"


async def _goto_google_login(page: Any, attempts: int = GOOGLE_LOGIN_ATTEMPTS) -> None:
    """Navigate to accounts.google.com and wait for the email-input form.

    Strategy:
      1. Navigate to accounts.google.com/signin — this always shows the
         email form for unauthenticated users.
      2. Wait for domcontentloaded, then check state.
      3. Retry on timeout.
    """
    url = _google_login_url()   # accounts.google.com/signin/v2/identifier
    last_error: Exception | None = None

    for attempt in range(attempts):
        try:
            await page.goto(
                url,
                wait_until="commit",
                timeout=LOGIN_NAVIGATION_TIMEOUT_MS,
            )
            # Wait for domcontentloaded on whatever page we land on.
            try:
                await page.wait_for_load_state("domcontentloaded", timeout=15000)
            except Exception:
                pass

            await page.wait_for_timeout(2000)
            state = await _google_login_state(page)
            if state != "UNKNOWN":
                return

            # Give one final generous wait before giving up
            await page.wait_for_timeout(5000)
            state = await _google_login_state(page)
            if state != "UNKNOWN":
                return

            raise PlaywrightTimeoutError(
                "Navigated to Google login but email form did not appear"
            )
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
    """Return True only when the page has settled on a confirmed logged-in Google URL.

    URL-only success is limited to Google account surfaces. Google One needs
    a content check because its public plans page also lives on one.google.com.
    """
    try:
        parsed = urlparse(url)
    except Exception:
        return False

    host = parsed.netloc.lower()
    return host in {"myaccount.google.com", "mail.google.com"}


def _is_google_identifier_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
    except Exception:
        return False
    host = parsed.netloc.lower()
    path = parsed.path.lower()
    return host == "accounts.google.com" and "signin" in path and "identifier" in path


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


async def _dismiss_new_device_confirm(page: Any) -> bool:
    """Auto-click the confirmation button on Google's new-device sign-in page.

    When Google detects a fresh browser/device it shows an interstitial:
      "You signed in on Android – Pixel 10 Pro"
    with buttons like "Yes, it's me", "Yes", or "Continue".
    Clicking any of these dismisses the page and resumes the flow.
    Returns True if a button was clicked.
    """
    confirm_selectors = [
        # Most specific first
        "button:has-text(\"Yes, it's me\")",
        "[role='button']:has-text(\"Yes, it's me\")",
        "button:has-text('Yes')",
        "[role='button']:has-text('Yes')",
        "button:has-text('Continue')",
        "[role='button']:has-text('Continue')",
        "button:has-text('Confirm')",
        "[role='button']:has-text('Confirm')",
        # Generic submit as last resort
        "input[type='submit']",
    ]
    return await _click_first_visible(page, confirm_selectors, timeout=4000)


async def _google_login_state(page: Any) -> str:
    if _is_google_login_success_url(page.url):
        return "SUCCESS"

    # one.google.com is excluded from _is_google_login_success_url to prevent
    # false-positives on the anonymous plans page. After credentials are
    # submitted Google may redirect back to one.google.com (authenticated).
    # We verify authenticity inline: no "Sign in" CTA + no anonymous markers.
    # IMPORTANT: check the URL HOST only — not the full URL string, because
    # the login URL contains continue=https://one.google.com/ in the query.
    try:
        _parsed_host = urlparse(page.url).netloc.lower()
    except Exception:
        _parsed_host = ""
    if _parsed_host == "one.google.com" or _parsed_host.endswith(".one.google.com"):
        _body = await _page_text(page)
        _ANON_MARKERS = (
            "choose the google one plan",
            "all google accounts come with up to 15 gb",
            "15 gb of storage",
            "this site uses cookies from google",
        )
        _signin_visible = (
            await _locator_visible(page, "a:has-text('Sign in')", timeout=400)
            or await _locator_visible(page, "button:has-text('Sign in')", timeout=400)
            or await _locator_visible(page, "[role='button']:has-text('Sign in')", timeout=400)
        )
        if not _signin_visible and not any(m in _body for m in _ANON_MARKERS):
            return "SUCCESS"

    challenge = await _detect_challenge(page)
    if challenge:
        return challenge

    text = await _page_text(page)
    # Check new-device confirmation BEFORE generic challenge markers
    # so we can auto-dismiss it in _wait_for_google_login_state.
    if _check_markers(text, _NEW_DEVICE_CONFIRM_MARKERS):
        return "NEW_DEVICE_CONFIRM"
    if _is_google_identifier_url(page.url):
        return "EMAIL"
    if any([await _locator_visible(page, selector) for selector in GOOGLE_EMAIL_SELECTORS]):
        return "EMAIL"
    if any([await _locator_visible(page, selector) for selector in GOOGLE_PASSWORD_SELECTORS]):
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
        "UNSAFE_BROWSER",
        "CAPTCHA",
        "UNUSUAL_ACTIVITY",
    }
    while time.time() < deadline:
        state = await _google_login_state(page)
        # Auto-dismiss Google's "You signed in on Pixel 10 Pro" interstitial.
        # This page is NOT a terminal state — just click through and keep polling.
        if state == "NEW_DEVICE_CONFIRM":
            clicked = await _dismiss_new_device_confirm(page)
            if clicked:
                await _wait_for_navigation(page, timeout=8000)
            await page.wait_for_timeout(700)
            continue
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
