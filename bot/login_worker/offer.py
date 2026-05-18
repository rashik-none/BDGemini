"""Google One Pixel/Gemini offer detection and claim flow."""

from __future__ import annotations

import logging
from html import escape as html_esc
from typing import Any
from urllib.parse import urljoin, urlparse

from .accounts import _update_job_status
from .config import _PAYMENT_REQUIRED_MARKERS
from .google_login import _wait_for_navigation
from .notify import _notify, _notify_photo
from .page import _check_markers, _mask_email, _page_text, _redact_sensitive, _screenshot

logger = logging.getLogger(__name__)


def _looks_like_offer_page(text: str, url: str) -> bool:
    lowered_url = url.lower()
    return (
        "offer" in lowered_url
        or "redeem" in lowered_url
        or "claim" in lowered_url
        or "gemini" in lowered_url
        or "google ai" in text
        or "ai premium" in text
    )


# ═══════════════════════════════════════════════════════════════════
#  OFFER CLAIM FLOW
# ═══════════════════════════════════════════════════════════════════

# Text markers that indicate the offer is ALREADY active.
# Must be specific enough NOT to match offer landing/promo pages.
_ALREADY_ACTIVE_MARKERS = [
    "you're subscribed",
    "you are subscribed",
    "premium member",
    "subscription started",
    "member since",
    "next billing date",
    "manage subscription",
    "cancel subscription",
    "cancel plan",
]

# Text / button labels that indicate a claimable offer.
_OFFER_BUTTON_MARKERS = [
    "start trial",
    "start free trial",
    "claim offer",
    "claim",
    "redeem",
    "get offer",
    "try at no cost",
    "check eligibility",
    "activate offer",
    "try google ai",
    "try gemini advanced",
    "try gemini",
    "get gemini advanced",
]
_OFFER_FOLLOWUP_BUTTON_MARKERS = [
    "accept and continue",
    "i agree",
    "continue",
    "confirm",
    "confirm purchase",
    "subscribe",
]

_PIXEL_OFFER_MARKERS = [
    "pixel",
    "giftbox",
    "gift box",
    "google ai pro",
    "gemini advanced",
    "google one ai premium",
]

_NOT_ELIGIBLE_MARKERS = [
    "not eligible",
    "isn't eligible",
    "not available",
    "offer isn't there",
    "offer has expired",
    "already redeemed",
    "can't redeem",
    "cannot redeem",
]


def _has_claimable_pixel_offer(text: str, url: str) -> bool:
    """Avoid treating generic Google One settings/plans pages as Pixel offers."""
    if _check_markers(text, _NOT_ELIGIBLE_MARKERS):
        return False
    if not _check_markers(text, _OFFER_BUTTON_MARKERS):
        return False
    url_lower = url.lower()
    if any(token in url_lower for token in ("offer", "redeem", "claim", "pixel")):
        return True
    return _check_markers(text, _PIXEL_OFFER_MARKERS)


def _score_offer_url(url: str) -> int:
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    target = f"{parsed.path.lower()}?{parsed.query.lower()}"
    score = 0

    if host == "one.google.com" or host.endswith(".one.google.com"):
        score += 50
    elif host.endswith(".google.com"):
        score += 10
    else:
        return 0

    if "/offer" in target:
        score += 45
        if parsed.path.rstrip("/").lower() != "/offers":
            score += 10
        else:
            score -= 10
    if any(token in target for token in ("redeem", "claim", "promo", "promotion")):
        score += 25
    if any(token in target for token in ("gemini", "ai-premium", "google-ai", "pixel")):
        score += 20
    if any(token in target for token in ("checkout", "subscribe", "subscription", "purchase")):
        score += 10
    if target.rstrip("?") in ("", "/", "/settings", "/about/plans", "/plans", "/storage"):
        score -= 30
    if any(token in target for token in ("/plans", "/storage", "g1_last_touchpoint")):
        score -= 35

    return score


async def _extract_offer_link(page: Any, *preferred_urls: str) -> str:
    """Return the best Google One offer/redeem URL visible on the page."""
    candidates: set[str] = set()
    base_url = page.url

    for url in (base_url, *preferred_urls):
        if url:
            candidates.add(urljoin(base_url, url))

    try:
        hrefs = await page.eval_on_selector_all(
            "a[href]",
            "els => els.map(e => e.getAttribute('href')).filter(Boolean)",
        )
    except Exception:
        hrefs = []

    for href in hrefs:
        if not isinstance(href, str):
            continue
        href = href.strip()
        if not href or href.startswith(("javascript:", "mailto:", "tel:")):
            continue
        candidates.add(urljoin(base_url, href))

    ranked = sorted(
        ((_score_offer_url(url), url) for url in candidates),
        key=lambda item: item[0],
        reverse=True,
    )
    if ranked and ranked[0][0] >= 50:
        return ranked[0][1]
    return ""


async def _claim_pixel_offer(
    page: Any,
    job_id: str,
    telegram_id: str,
    bot: Any,
    chat_id: int,
    gmail: str,
) -> str:
    """Navigate Google One and try to claim the Pixel Gemini offer.

    Returns one of:
      "CLAIMED"          – offer was successfully claimed
      "ALREADY_ACTIVE"   – subscription already exists
      "NOT_FOUND"        – no eligible offer detected
      "CLAIM_FAILED"     – found offer but couldn't complete claim
    """
    masked_email = _mask_email(gmail)
    try:
        await _notify(
            bot, chat_id,
            f"🎁 <b>Job {html_esc(job_id)}</b>\n\n"
            "<b>Claiming offer</b>\n"
            "Opening Google One and checking Pixel/Gemini eligibility.",
        )

        # ── Go to Google One home ──────────────────────────────────
        await page.goto("https://one.google.com/", wait_until="networkidle")
        await page.wait_for_timeout(3000)
        await _screenshot(page, job_id, "08_one_home")

        body = await _page_text(page)

        # Check if already subscribed
        if any(m in body for m in _ALREADY_ACTIVE_MARKERS):
            ss = await _screenshot(page, job_id, "08_already_active")
            await _notify(
                bot, chat_id,
                f"ℹ️ <b>Job {html_esc(job_id)}</b>\n\n"
                "<b>Plan already active</b>\n"
                f"Account: <code>{html_esc(masked_email)}</code>",
            )
            if ss:
                await _notify_photo(bot, chat_id, ss, "Already subscribed")
            return "ALREADY_ACTIVE"

        # ── Strategy 1: check Settings → Offers ────────────────────
        offer_found = False
        offer_page_url = ""
        initial_offer_link = ""

        try:
            await page.goto(
                "https://one.google.com/settings", wait_until="networkidle"
            )
            await page.wait_for_timeout(2000)
            await _screenshot(page, job_id, "09_settings")

            # Look for "Check for offers" or similar
            for label in ["Check for offers", "Offers", "Promotions"]:
                try:
                    btn = page.locator(f"text={label}").first
                    if await btn.is_visible(timeout=3000):
                        await btn.click()
                        await _wait_for_navigation(page)
                        await page.wait_for_timeout(3000)
                        body = await _page_text(page)
                        if _has_claimable_pixel_offer(body, page.url):
                            offer_found = True
                            offer_page_url = page.url
                            break
                except Exception:
                    continue
        except Exception:
            pass

        # ── Strategy 2: direct offer redemption URLs ───────────────
        if not offer_found:
            offer_urls = [
                # Pixel device offer direct redeem
                "https://one.google.com/u/0/offers/redeem/pixel",
                # Generic offers page
                "https://one.google.com/offers",
                # Gemini Advanced plans page
                "https://one.google.com/intl/en/about/plans",
                # Older path still sometimes works
                "https://one.google.com/about/plans",
            ]
            for url in offer_urls:
                try:
                    await page.goto(url, wait_until="networkidle")
                    await page.wait_for_timeout(3000)
                    body = await _page_text(page)
                    # Check for offer buttons but NOT already-active markers
                    already = any(m in body for m in _ALREADY_ACTIVE_MARKERS)
                    has_offer = _has_claimable_pixel_offer(body, page.url)
                    if has_offer and not already:
                        offer_found = True
                        offer_page_url = page.url
                        logger.info("[%s] Offer found at: %s", job_id, page.url)
                        break
                    elif already:
                        logger.info("[%s] Already subscribed detected at %s", job_id, url)
                        await _notify(
                            bot, chat_id,
                            f"ℹ️ <b>Job {html_esc(job_id)}</b>\n\n"
                            "<b>Plan already active</b>\n"
                            f"Account: <code>{html_esc(masked_email)}</code>",
                        )
                        return "ALREADY_ACTIVE"
                except Exception:
                    continue

        await _screenshot(page, job_id, "10_offer_page")

        if not offer_found:
            # Last check — maybe the page has offer buttons anyway
            body = await _page_text(page)
            if _has_claimable_pixel_offer(body, page.url):
                offer_found = True
                offer_page_url = page.url

        if not offer_found:
            ss = await _screenshot(page, job_id, "10_no_offer")
            await _notify(
                bot, chat_id,
                f"⚠️ <b>Job {html_esc(job_id)}</b>\n\n"
                "<b>No eligible offer found</b>\n"
                "This account does not currently show a Pixel/Gemini offer.",
            )
            if ss:
                await _notify_photo(bot, chat_id, ss, "No offer found")
            return "NOT_FOUND"

        offer_page_url = offer_page_url or page.url
        initial_offer_link = await _extract_offer_link(page, offer_page_url)
        status_extra = {"offer_page_url": offer_page_url}
        if initial_offer_link:
            status_extra["redeem_link"] = initial_offer_link
        await _update_job_status(telegram_id, job_id, "PROCESSING", status_extra)

        # ── Click through the claim flow ───────────────────────────
        await _notify(
            bot, chat_id,
            f"🎁 <b>Job {html_esc(job_id)}</b>\n\n"
            "<b>Offer found</b>\n"
            "Starting the claim flow.",
        )

        # Try explicit offer buttons first; only use generic continuation on
        # offer-specific pages to avoid walking unrelated Google One flows.
        claimed = False
        for attempt_btn in range(5):  # up to 5 screens of buttons
            body = await _page_text(page)
            clicked = False

            labels = list(_OFFER_BUTTON_MARKERS)
            if _looks_like_offer_page(body, page.url):
                labels.extend(_OFFER_FOLLOWUP_BUTTON_MARKERS)

            for label in labels:
                try:
                    # Broad selector covering standard + Google Material/Web components
                    btns = page.locator(
                        f"button:has-text('{label}'), "
                        f"a:has-text('{label}'), "
                        f"[role='button']:has-text('{label}'), "
                        f"mwc-button:has-text('{label}'), "
                        f"material-button:has-text('{label}'), "
                        f"[jsname]:has-text('{label}')"
                    )
                    count = await btns.count()
                    if count > 0:
                        btn = btns.first
                        if await btn.is_visible(timeout=3000):
                            logger.info(
                                "[%s] Clicking offer button: '%s'",
                                job_id, label,
                            )
                            await btn.click()
                            await _wait_for_navigation(page)
                            await page.wait_for_timeout(3000)
                            await _screenshot(
                                page, job_id,
                                f"11_claim_step_{attempt_btn}",
                            )
                            clicked = True
                            break
                except Exception:
                    continue

            # Also try JS-click as fallback for shadow DOM / hidden buttons
            if not clicked:
                try:
                    js_clicked = await page.evaluate('''
                        (labels) => {
                            for (const label of labels) {
                                const lower = label.toLowerCase();
                                for (const el of document.querySelectorAll(
                                    'button, a, [role="button"], mwc-button'
                                )) {
                                    const txt = (el.innerText || el.textContent || "").trim().toLowerCase();
                                    if (txt.includes(lower) && el.offsetParent !== null) {
                                        el.click();
                                        return label;
                                    }
                                }
                            }
                            return null;
                        }
                    ''', labels)
                    if js_clicked:
                        logger.info("[%s] JS-clicked offer button: '%s'", job_id, js_clicked)
                        await _wait_for_navigation(page)
                        await page.wait_for_timeout(3000)
                        clicked = True
                except Exception:
                    pass

            if not clicked:
                break

            # Check if we reached a confirmation / success state
            body = await _page_text(page)
            success_markers = [
                "you're all set",
                "welcome to",
                "successfully",
                "subscription started",
                "your plan",
                "enjoy your",
                "trial activated",
                "plan confirmed",
            ]
            if any(m in body for m in success_markers):
                claimed = True
                break
            if _check_markers(body, _PAYMENT_REQUIRED_MARKERS):
                await _update_job_status(
                    telegram_id, job_id, "PROCESSING",
                    {
                        "claim_result_url": page.url,
                        "offer_page_url": offer_page_url,
                        "progress_note": "Payment method required",
                    },
                )
                break

        ss = await _screenshot(page, job_id, "12_claim_result")

        if claimed:
            redeem_link = await _extract_offer_link(
                page, page.url, offer_page_url, initial_offer_link,
            )
            claim_extra = {
                "claim_result_url": page.url,
                "offer_page_url": offer_page_url,
            }
            if redeem_link:
                claim_extra["redeem_link"] = redeem_link
            await _update_job_status(telegram_id, job_id, "PROCESSING", claim_extra)

            await _notify(
                bot, chat_id,
                f"🎉 <b>Job {html_esc(job_id)}</b>\n\n"
                "<b>Offer claimed successfully</b>\n"
                f"Account: <code>{html_esc(masked_email)}</code>\n"
                "Google One AI Premium / Gemini Advanced activated."
                + (f"\n\n🔗 Redeem link: {redeem_link}" if redeem_link else ""),
            )
            if ss:
                await _notify_photo(bot, chat_id, ss, "🎉 Offer claimed!")
            return "CLAIMED"

        # Check once more — subscription may have activated silently
        body = await _page_text(page)
        if any(m in body for m in _ALREADY_ACTIVE_MARKERS):
            redeem_link = await _extract_offer_link(
                page, page.url, offer_page_url, initial_offer_link,
            )
            claim_extra = {
                "claim_result_url": page.url,
                "offer_page_url": offer_page_url,
            }
            if redeem_link:
                claim_extra["redeem_link"] = redeem_link
            await _update_job_status(telegram_id, job_id, "PROCESSING", claim_extra)
            await _notify(
                bot, chat_id,
                f"🎉 <b>Job {html_esc(job_id)}</b>\n\n"
                "<b>Plan active</b>\n"
                f"Account: <code>{html_esc(masked_email)}</code>"
                + (f"\n\n🔗 Redeem link: {redeem_link}" if redeem_link else ""),
            )
            if ss:
                await _notify_photo(bot, chat_id, ss, "Plan active")
            return "CLAIMED"

        # We found the offer but couldn't fully complete
        redeem_link = await _extract_offer_link(
            page, page.url, offer_page_url, initial_offer_link,
        )
        body = await _page_text(page)
        needs_payment = _check_markers(body, _PAYMENT_REQUIRED_MARKERS)
        claim_extra = {
            "claim_result_url": page.url,
            "offer_page_url": offer_page_url,
            "progress_note": (
                "Payment method required" if needs_payment else "Claim requires manual completion"
            ),
        }
        if redeem_link:
            claim_extra["redeem_link"] = redeem_link
        await _update_job_status(telegram_id, job_id, "PROCESSING", claim_extra)
        message = (
            "Offer checkout requires a valid payment method and final Subscribe tap."
            if needs_payment
            else "Offer was found but claim needs manual completion."
        )
        await _notify(
            bot, chat_id,
            f"⚠️ <b>Job {html_esc(job_id)}</b>\n\n"
            "<b>Claim needs attention</b>\n"
            f"{message}"
            + (f"\n\n🔗 Offer link: {redeem_link}" if redeem_link else ""),
        )
        if ss:
            await _notify_photo(
                bot, chat_id, ss,
                "Claim incomplete — may need payment method",
            )
        return "CLAIM_FAILED"

    except Exception as exc:
        safe_error = _redact_sensitive(str(exc), gmail)
        logger.warning("[%s] Offer claim crashed: %s", job_id, safe_error)
        ss = await _screenshot(page, job_id, "claim_error")
        await _notify(
            bot, chat_id,
            f"💥 <b>Job {html_esc(job_id)}</b>\n\n"
            "<b>Offer claim error</b>\n"
            f"<code>{html_esc(safe_error[:200])}</code>",
        )
        if ss:
            await _notify_photo(bot, chat_id, ss, "Claim error")
        return "CLAIM_FAILED"


# ═══════════════════════════════════════════════════════════════════
#  CORE LOGIN FLOW
