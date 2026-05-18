"""Login job orchestration."""

from __future__ import annotations

import asyncio
import logging
import random
import time
from html import escape as html_esc
from typing import Any

from .accounts import _refund_job, _update_job_status
from .browser import (
    _block_heavy_resources,
    _launch_android_browser,
    _random_pause,
)
from .config import (
    ANDROID_DPR,
    ANDROID_USER_AGENT,
    ANDROID_VIEWPORT,
    CLIENT_HINTS_HEADERS,
    DEVICE_PROMPT_TIMEOUT,
    MAX_RETRIES,
)
from .google_login import (
    _click_first_visible,
    _find_totp_selector,
    _goto_google_login,
    _is_google_login_success_url,
    _open_device_prompt_challenge,
    _open_totp_challenge,
    _wait_for_google_login_state,
    _wait_for_navigation,
    _wait_for_visible_selector,
)
from .notify import _notify, register_job_message
from .offer import _claim_pixel_offer
from .page import (
    _detect_challenge,
    _human_type,
    _mask_email,
    _redact_sensitive,
    _safe_proxy_label,
    _screenshot,
)
from .proxy import _load_proxy_list, _pick_proxy
from .totp import _extract_totp_secret, _generate_totp, _is_totp_method

logger = logging.getLogger(__name__)

ATTEMPT_SUCCESS = "success"
ATTEMPT_TERMINAL = "terminal"
ATTEMPT_RETRY = "retry"


async def _do_login_attempt(
    gmail: str,
    password: str,
    method: str,
    job_id: str,
    telegram_id: str,
    bot: Any,
    chat_id: int,
    proxy: dict[str, str] | None,
    attempt: int,
) -> str:
    """Run one login attempt and classify the outcome for the retry loop."""

    masked_email = _mask_email(gmail)
    login_email = gmail
    login_password = password
    verification_method = method
    password = ""
    method = ""
    proxy_label = _safe_proxy_label(proxy)
    logger.info("[%s] attempt=%d  proxy=%s", job_id, attempt, proxy_label)
    await _notify(
        bot,
        chat_id,
        f"⏳ <b>Job {html_esc(job_id)}</b>\n\n"
        f"<b>Starting secure session</b>\n"
        f"Attempt {attempt + 1}/{MAX_RETRIES + 1}\n"
        f"Account: <code>{html_esc(masked_email)}</code>",
    )

    async with _launch_android_browser(proxy) as browser:
        # Firefox (invisible_playwright) does NOT support is_mobile / has_touch.
        # Mobile UA is already injected via general.useragent.override pref.
        # NOTE: ignore_https_errors is also NOT supported by Firefox Playwright
        #   (throws NS_ERROR_NOT_AVAILABLE). SSL bypass is done via prefs in browser.py.
        context = await browser.new_context(
            user_agent=ANDROID_USER_AGENT,
            viewport=ANDROID_VIEWPORT,
            device_scale_factor=ANDROID_DPR,
            locale="en-US",
            timezone_id="Asia/Dhaka",
            extra_http_headers=CLIENT_HINTS_HEADERS,
        )
        try:
            await _block_heavy_resources(context)
            # No _add_stealth needed — invisible_playwright patches at C++ level.
            # JS overrides would be detectable and hurt reCAPTCHA scores.
            page = await context.new_page()
            # Residential proxy can be slow — raise all page-level timeouts.
            page.set_default_navigation_timeout(90_000)  # 90 s
            page.set_default_timeout(60_000)             # 60 s for selectors
            await _random_pause(page, 1500, 3000)

            # ── 1. Navigate ────────────────────────────────────────────
            logger.info("[%s] → Google login", job_id)
            await _update_job_status(
                telegram_id,
                job_id,
                "PROCESSING",
                {"progress": 10, "progress_note": "Opening Google login…"},
            )
            await _goto_google_login(page)
            await _wait_for_navigation(page)
            await _screenshot(page, job_id, "01_landing")

            # ── 2. Email ───────────────────────────────────────────────
            logger.info("[%s] → email", job_id)
            await _update_job_status(
                telegram_id,
                job_id,
                "PROCESSING",
                {"progress": 25, "progress_note": "Submitting Google email"},
            )
            await _notify(
                bot,
                chat_id,
                f"📧 <b>Job {html_esc(job_id)}</b>\n\n"
                "<b>Checking Gmail account</b>\n"
                "Submitting email and waiting for Google's next step.",
            )

            login_state = await _wait_for_google_login_state(page, {"EMAIL", "SUCCESS"}, timeout=20000)
            if login_state == "SUCCESS":
                logger.info("[%s] existing Google session detected", job_id)
            elif login_state != "EMAIL":
                await _screenshot(page, job_id, f"02_unexpected_{login_state}")
                await _notify(
                    bot, chat_id,
                    f"⚠️ <b>Job {html_esc(job_id)}</b>\n"
                    f"Google login did not show email input: <code>{login_state}</code>",
                )
                return ATTEMPT_RETRY

            if login_state == "EMAIL":
                email_selector = await _wait_for_visible_selector(
                    page,
                    ['input[type="email"]', "#identifierId"],
                    timeout=20000,
                )
                if not email_selector:
                    return ATTEMPT_RETRY
                await _human_type(page, email_selector, login_email)
                login_email = ""

                clicked = await _click_first_visible(
                    page,
                    [
                        "#identifierNext",
                        "button:has-text('Next')",
                        "input[type='submit']",
                    ],
                    timeout=8000,
                )
                if not clicked:
                    await page.keyboard.press("Enter")
                await _wait_for_navigation(page)

            login_state = await _wait_for_google_login_state(
                page,
                {"PASSWORD", "SUCCESS"},
                timeout=30000,
            )
            if login_state in {"WRONG_PASSWORD", "ACCOUNT_LOCKED", "CAPTCHA", "UNUSUAL_ACTIVITY"}:
                await _screenshot(page, job_id, f"03_challenge_{login_state}")
                await _notify(
                    bot, chat_id,
                    f"⚠️ <b>Job {html_esc(job_id)}</b>\n"
                    f"Challenge after email: <code>{login_state}</code>",
                )
                return ATTEMPT_RETRY

            # ── 3. Password ───────────────────────────────────────────
            if login_state == "SUCCESS":
                logger.info("[%s] skipping password — already authenticated", job_id)
            else:
                if login_state != "PASSWORD":
                    await _screenshot(page, job_id, f"04_unexpected_{login_state}")
                    await _notify(
                        bot, chat_id,
                        f"⚠️ <b>Job {html_esc(job_id)}</b>\n"
                        f"Password input not reached: <code>{login_state}</code>",
                    )
                    return ATTEMPT_RETRY

                logger.info("[%s] → password", job_id)
                await _update_job_status(
                    telegram_id,
                    job_id,
                    "PROCESSING",
                    {"progress": 40, "progress_note": "Submitting Google password"},
                )
                await _notify(
                    bot,
                    chat_id,
                    f"🔑 <b>Job {html_esc(job_id)}</b>\n\n"
                    "<b>Submitting credentials</b>\n"
                    "Password submitted securely. Waiting for verification.",
                )

                pwd_selector = await _wait_for_visible_selector(
                    page,
                    ['input[type="password"]', 'input[name="Passwd"]'],
                    timeout=20000,
                )
                if not pwd_selector:
                    await _screenshot(page, job_id, "04_no_password_field")
                    await _notify(
                        bot, chat_id,
                        f"⚠️ <b>Job {html_esc(job_id)}</b>\n"
                        "Password field not found.",
                    )
                    return ATTEMPT_RETRY

                await _random_pause(page, 400, 1000)
                await _human_type(page, pwd_selector, login_password)
                login_password = ""

                clicked = await _click_first_visible(
                    page,
                    [
                        "#passwordNext",
                        "button:has-text('Next')",
                        "button:has-text('Verify')",
                        "input[type='submit']",
                    ],
                    timeout=8000,
                )
                if not clicked:
                    await page.keyboard.press("Enter")
                await _wait_for_navigation(page)
                await _random_pause(page, 2000, 4000)

                login_state = await _wait_for_google_login_state(
                    page,
                    {"SUCCESS", "TOTP", "DEVICE_PROMPT", "TRY_ANOTHER_WAY"},
                    timeout=30000,
                )
                if login_state in {"WRONG_PASSWORD", "ACCOUNT_LOCKED", "CAPTCHA", "UNUSUAL_ACTIVITY"}:
                    await _screenshot(page, job_id, f"05_challenge_{login_state}")
                    await _notify(
                        bot, chat_id,
                        f"⚠️ <b>Job {html_esc(job_id)}</b>\n"
                        f"Challenge after password: <code>{login_state}</code>",
                    )
                    if login_state == "WRONG_PASSWORD":
                        await _update_job_status(
                            telegram_id,
                            job_id,
                            "FAILED",
                            {
                                "progress": 100,
                                "progress_note": "Wrong password",
                                "error": "wrong_password",
                            },
                        )
                        await _refund_job(telegram_id, job_id)
                        await _notify(
                            bot, chat_id,
                            f"❌ <b>Job {html_esc(job_id)}</b>\n"
                            "Login failed — wrong password.\n\n"
                            "▶️ 1 credit has been refunded to your balance.",
                        )
                        return ATTEMPT_TERMINAL
                    return ATTEMPT_RETRY

            # ── 4. Verification method ─────────────────────────────────
            await _update_job_status(
                telegram_id,
                job_id,
                "PROCESSING",
                {"progress": 60, "progress_note": "Handling verification…"},
            )
            if login_state == "SUCCESS":
                pass
            elif _is_totp_method(verification_method):
                logger.info("[%s] → 2FA TOTP", job_id)
                await _notify(
                    bot, chat_id,
                    f"🔐 <b>Job {html_esc(job_id)}</b>\n\n"
                    "<b>Waiting for verification</b>\n"
                    "Preparing 2FA code and completing the sign-in step.",
                )
                totp_secret = _extract_totp_secret(verification_method)
                verification_method = ""
                if not totp_secret:
                    await _update_job_status(
                        telegram_id, job_id, "FAILED",
                        {"progress": 100,
                         "progress_note": "Missing TOTP secret",
                         "error": "missing_totp_secret"},
                    )
                    await _refund_job(telegram_id, job_id)
                    await _notify(
                        bot, chat_id,
                        f"❌ <b>Job {html_esc(job_id)}</b>\n"
                        "2FA Secret method selected, but no TOTP secret was provided.\n\n"
                        "▶️ 1 credit has been refunded to your balance.",
                    )
                    return ATTEMPT_TERMINAL

                totp_selector = await _find_totp_selector(page, timeout=8000)
                if not totp_selector:
                    await _open_totp_challenge(page)
                    totp_selector = await _find_totp_selector(page, timeout=8000)
                totp_found = bool(totp_selector)

                if totp_found:
                    try:
                        totp_code = _generate_totp(totp_secret)
                        totp_secret = ""
                    except ValueError as exc:
                        totp_secret = ""
                        await _update_job_status(
                            telegram_id, job_id, "FAILED",
                            {"progress": 100,
                             "progress_note": str(exc),
                             "error": "invalid_totp_secret"},
                        )
                        await _refund_job(telegram_id, job_id)
                        await _notify(
                            bot, chat_id,
                            f"❌ <b>Job {html_esc(job_id)}</b>\n"
                            "Invalid TOTP secret. Please provide a valid base32 secret.\n\n"
                            "▶️ 1 credit has been refunded to your balance.",
                        )
                        return ATTEMPT_TERMINAL

                    await _human_type(page, totp_selector, totp_code)
                    totp_code = ""
                    submitted = False
                    for next_selector in [
                        "#totpNext",
                        "button:has-text('Next')",
                        "button:has-text('Verify')",
                        "input[type='submit']",
                    ]:
                        try:
                            next_btn = page.locator(next_selector).first
                            if await next_btn.is_visible(timeout=3000):
                                await next_btn.click()
                                submitted = True
                                break
                        except Exception:
                            continue
                    if not submitted:
                        await page.keyboard.press("Enter")
                    await _wait_for_navigation(page)
                    login_state = await _wait_for_google_login_state(
                        page,
                        {"SUCCESS", "DEVICE_PROMPT", "TRY_ANOTHER_WAY"},
                        timeout=15000,
                    )
                    await _notify(
                        bot, chat_id,
                        f"🔐 <b>Job {html_esc(job_id)}</b>\n\n"
                        "<b>Verification submitted</b>\n"
                        "2FA code accepted. Checking login result.",
                    )
                else:
                    totp_secret = ""
                    await _screenshot(page, job_id, "06_no_totp")
                    await _notify(
                        bot, chat_id,
                        f"⚠️ <b>Job {html_esc(job_id)}</b>\n"
                        "TOTP input not found — Google may show a different challenge.",
                    )

            else:  # Verify sign-in (device prompt)
                logger.info("[%s] → device prompt", job_id)
                if login_state == "TRY_ANOTHER_WAY":
                    await _open_device_prompt_challenge(page)
            
                await page.wait_for_timeout(2000)
            
                tap_number = None
                try:
                    tap_number = await page.evaluate('''
                        () => {
                            const bodyTxt = document.body.innerText || "";
                            const m = bodyTxt.match(/tap\\s*(?:on\\s*)?(?:number\\s*)?(\\d{1,2})/i);
                            if (m) return m[1];
                        
                            for (const el of document.querySelectorAll('*')) {
                                if (el.children.length === 0) {
                                    const txt = (el.innerText || "").trim();
                                    if (/^\\d{1,2}$/.test(txt)) {
                                        const style = window.getComputedStyle(el);
                                        if (parseInt(style.fontSize) > 18 && style.display !== 'none') {
                                            return txt;
                                        }
                                    }
                                }
                            }
                            return null;
                        }
                    ''')
                except Exception:
                    pass

                if tap_number:
                    prompt_text = (
                        f"📱 <b>Job {html_esc(job_id)}</b>\n\n"
                        "<b>Action needed on your phone</b>\n"
                        f"Tap <b>Yes</b> and then tap <b>{tap_number}</b> on your phone within {DEVICE_PROMPT_TIMEOUT}s."
                    )
                else:
                    prompt_text = (
                        f"📱 <b>Job {html_esc(job_id)}</b>\n\n"
                        "<b>Action needed on your phone</b>\n"
                        f"Tap <b>Yes</b> on your phone within {DEVICE_PROMPT_TIMEOUT}s."
                    )

                await _notify(bot, chat_id, prompt_text)
                await _screenshot(page, job_id, "06_device_prompt")

                # Poll until URL changes or timeout
                deadline = time.time() + DEVICE_PROMPT_TIMEOUT
                while time.time() < deadline:
                    url = page.url
                    if _is_google_login_success_url(url):
                        login_state = "SUCCESS"
                        break
                    # Also detect if Google gave up
                    ch = await _detect_challenge(page)
                    if ch:
                        login_state = ch
                        break
                    await page.wait_for_timeout(3000)

            # ── 5. Result check ────────────────────────────────────────
            await page.wait_for_timeout(3000)
            current_url = page.url
            await _screenshot(page, job_id, "07_result")

            if _is_google_login_success_url(current_url):
                logger.info("[%s] ✓ LOGIN SUCCESS", job_id)
                await _notify(
                    bot, chat_id,
                    f"✅ <b>Job {html_esc(job_id)}</b>\n\n"
                    "<b>Login verified</b>\n"
                    f"Account: <code>{html_esc(masked_email)}</code>\n"
                    "Now claiming the Pixel offer.",
                )
                # ── 6. Claim Google One / Gemini Advanced offer ────────
                claim_result = await _claim_pixel_offer(
                    page, job_id, telegram_id, bot, chat_id, gmail,
                )

                if claim_result == "CLAIMED":
                    await _update_job_status(
                        telegram_id,
                        job_id,
                        "SUCCEEDED",
                        {"progress": 100, "progress_note": "Completed successfully"},
                    )
                elif claim_result == "ALREADY_ACTIVE":
                    await _update_job_status(
                        telegram_id,
                        job_id,
                        "SUCCEEDED",
                        {
                            "progress": 100,
                            "progress_note": "Completed successfully (plan already active)",
                        },
                    )
                else:
                    await _update_job_status(
                        telegram_id,
                        job_id,
                        "FAILED",
                        {"progress": 100, "progress_note": claim_result, "error": claim_result},
                    )
                    await _refund_job(telegram_id, job_id)
                    await _notify(
                        bot, chat_id,
                        f"▶️ <b>Job {html_esc(job_id)}</b>\n"
                        "1 credit has been refunded to your balance.",
                    )

                return ATTEMPT_SUCCESS if claim_result in {"CLAIMED", "ALREADY_ACTIVE"} else ATTEMPT_TERMINAL

            # Not on success page
            logger.warning("[%s] ✗ ended on %s", job_id, current_url)
            return ATTEMPT_RETRY


        finally:
            await context.close()

# ═══════════════════════════════════════════════════════════════════
#  ORCHESTRATOR (retry loop)
# ═══════════════════════════════════════════════════════════════════

async def _run_login_job(
    gmail: str,
    password: str,
    method: str,
    job_id: str,
    telegram_id: str,
    bot: Any,
    chat_id: int,
) -> None:
    """Retry-aware orchestrator for the login job."""

    masked_email = _mask_email(gmail)
    await _update_job_status(
        telegram_id,
        job_id,
        "PROCESSING",
        {"progress": 5, "progress_note": "Starting…"},
    )
    proxies = _load_proxy_list()

    if proxies:
        logger.info("[%s] Proxy pool: %d proxies loaded", job_id, len(proxies))
    else:
        logger.info("[%s] No proxies configured — using direct connection", job_id)

    last_error: str = ""
    try:
        for attempt in range(MAX_RETRIES + 1):
            proxy = _pick_proxy(proxies, attempt)

            try:
                attempt_result = await _do_login_attempt(
                    gmail,
                    password,
                    method,
                    job_id,
                    telegram_id,
                    bot,
                    chat_id,
                    proxy,
                    attempt,
                )
                if attempt_result in {ATTEMPT_SUCCESS, ATTEMPT_TERMINAL}:
                    return
                last_error = "login_flow_failed"

            except Exception as exc:
                last_error = _redact_sensitive(
                    f"{type(exc).__name__}: {exc}",
                    gmail, password, method,
                )
                logger.warning("[%s] attempt %d crashed: %s", job_id, attempt, last_error)
                await _notify(
                    bot, chat_id,
                    f"💥 <b>Job {html_esc(job_id)}</b>\n\n"
                    f"<b>Attempt {attempt + 1} failed</b>\n"
                    f"<code>{html_esc(last_error[:200])}</code>",
                )

            # Back-off before retry
            if attempt < MAX_RETRIES:
                wait = (2 ** attempt) + random.uniform(1, 3)
                logger.info("[%s] Retrying in %.1fs…", job_id, wait)
                await _notify(
                    bot, chat_id,
                    f"🔄 <b>Job {html_esc(job_id)}</b>\n\n"
                    "<b>Retry scheduled</b>\n"
                    f"Next attempt starts in {int(wait)}s.\n"
                    f"Attempt {attempt + 2}/{MAX_RETRIES + 1}",
                )
                await asyncio.sleep(wait)

        # All attempts exhausted
        await _update_job_status(
            telegram_id,
            job_id,
            "FAILED",
            {"progress": 100, "progress_note": last_error[:200], "error": last_error[:200]},
        )
        await _refund_job(telegram_id, job_id)
        await _notify(
            bot, chat_id,
            f"❌ <b>Job {html_esc(job_id)}</b>\n\n"
            "<b>Job failed</b>\n"
            f"Account: <code>{html_esc(masked_email)}</code>\n"
            f"Reason: <code>{html_esc(last_error[:150])}</code>\n\n"
            "↩️ 1 credit has been refunded to your balance.",
        )


    except asyncio.CancelledError:
        logger.warning("[%s] Job was cancelled abruptly", job_id)
        await _update_job_status(
            telegram_id,
            job_id,
            "FAILED",
            {"progress": 100, "progress_note": "Job cancelled", "error": "cancelled"},
        )
        await _refund_job(telegram_id, job_id)
        raise

# ═══════════════════════════════════════════════════════════════════
#  PUBLIC API
# ═══════════════════════════════════════════════════════════════════

def start_login_job(
    gmail: str,
    password: str,
    method: str,
    job_id: str,
    telegram_id: str,
    bot: Any,
    chat_id: int,
    message_id: int | None = None,
) -> asyncio.Task:
    """Schedule the login as a fire-and-forget background task.

    Returns the asyncio.Task so the caller can await / cancel if needed.
    """
    if message_id:
        register_job_message(job_id, message_id)

    return asyncio.create_task(
        _run_login_job(gmail, password, method, job_id, telegram_id, bot, chat_id),
        name=f"login-{job_id}",
    )
