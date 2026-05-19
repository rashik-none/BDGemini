"""Google One Pixel/Gemini offer detection and claim flow."""

from __future__ import annotations

import logging
from html import escape as html_esc
from typing import Any
from urllib.parse import urljoin, urlparse

from bot.accounts import update_job_status as _update_job_status
from .config import _PAYMENT_REQUIRED_MARKERS
from .google_login import _wait_for_navigation
from .humanize import _dwell_before_action, _human_scroll, _simulate_touch
from .notify import _notify, _notify_photo
from .page import _check_markers, _mask_email, _page_text, _redact_sensitive, _screenshot

logger = logging.getLogger(__name__)

OFFER_CLAIMED = "CLAIMED"
OFFER_ALREADY_ACTIVE = "ALREADY_ACTIVE"
OFFER_NOT_ELIGIBLE = "NOT_ELIGIBLE"
OFFER_NOT_FOUND = "NOT_FOUND"
OFFER_PAYMENT_REQUIRED = "PAYMENT_REQUIRED"
OFFER_MANUAL_REQUIRED = "MANUAL_REQUIRED"
OFFER_CLAIM_FAILED = "CLAIM_FAILED"
OFFER_CLAIMABLE = "CLAIMABLE"
OFFER_UNKNOWN = "UNKNOWN"


# ── helpers ──────────────────────────────────────────────────────────────────

async def _goto_one(page: Any, path: str = "/") -> None:
    """Navigate to a Google One path and wait for the SPA to settle.

    Google One is a React/Angular SPA — it NEVER fires 'networkidle'.
    We use domcontentloaded + a dynamic content-based settle approach:
      1. Wait for domcontentloaded (or timeout gracefully)
      2. Poll for meaningful body text up to 8 seconds
      3. Minimum 2s floor to allow Angular/React hydration
    """
    url = f"https://one.google.com{path}"
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
    except Exception:
        # Timeout on domcontentloaded is common with slow proxies; the page
        # content is usually there anyway.
        pass

    # Dynamic settle: wait until the SPA renders meaningful content
    # (body text > 50 chars indicates real UI, not just a loader shell)
    for _ in range(16):  # up to 8 seconds (16 × 500ms)
        await page.wait_for_timeout(500)
        try:
            body_len = await page.evaluate("document.body?.innerText?.length || 0")
            if body_len > 50:
                break
        except Exception:
            pass
    # Minimum 1s floor after content detected for hydration
    await page.wait_for_timeout(1000)


async def _page_body(page: Any) -> str:
    """Lowercase body text for marker matching."""
    try:
        return (await page.inner_text("body")).lower()
    except Exception:
        return ""


async def _locator_any_visible(page: Any, selector: str, timeout: int = 500) -> bool:
    """Return True if any matching element is visible.

    Google pages often keep hidden duplicate nav buttons in the DOM. Checking
    only `.first` can miss the visible header button.
    """
    try:
        loc = page.locator(selector)
        try:
            count = min(await loc.count(), 10)
            candidates = [loc.nth(i) for i in range(count)]
        except Exception:
            candidates = [loc.first]

        for el in candidates:
            try:
                if await el.is_visible(timeout=timeout):
                    return True
            except Exception:
                continue
    except Exception:
        return False
    return False


async def _google_one_signin_visible(page: Any) -> bool:
    selectors = [
        "a:has-text('Sign in')",
        "button:has-text('Sign in')",
        "[role='button']:has-text('Sign in')",
        "[aria-label*='Sign in' i]",
    ]
    for selector in selectors:
        if await _locator_any_visible(page, selector, timeout=500):
            return True
    return False


def _is_google_one_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
    except Exception:
        return False
    host = parsed.netloc.lower()
    return host == "one.google.com" or host.endswith(".one.google.com")


def _looks_like_anonymous_google_one_page(text: str, url: str) -> bool:
    """Detect the public Google One shell/pricing page.

    This page contains generic "Get started" and Google AI plan text, so it can
    otherwise look claimable even though the account is not signed in.
    """
    if not _is_google_one_url(url):
        return False
    if "sign in" not in text:
        return False
    return any(
        marker in text
        for marker in (
            "choose the google one plan",
            "all google accounts come with up to 15 gb",
            "15 gb of storage",
            "this site uses cookies from google",
        )
    )


async def _google_one_authenticated_page(page: Any) -> bool:
    if not _is_google_one_url(page.url):
        return False
    if await _google_one_signin_visible(page):
        return False
    body = await _page_body(page)
    return not _looks_like_anonymous_google_one_page(body, page.url)


async def _ensure_google_one_authenticated(page: Any, job_id: str) -> bool:
    """Return True only when Google One no longer shows the anonymous Sign in CTA."""
    if await _google_one_authenticated_page(page):
        return True

    await _screenshot(page, job_id, "08_google_one_signin_required")
    clicked = await _click_offer_button(page, ["sign in"], timeout=5000)
    if not clicked:
        return False

    await _wait_for_navigation(page, timeout=10_000)
    for _ in range(10):
        if await _google_one_authenticated_page(page):
            return True
        await page.wait_for_timeout(1000)
    return False


async def _dismiss_cookie_consent(page: Any) -> None:
    """Dismiss EU/GDPR cookie consent banners that overlay offer buttons.

    Google uses several variants across locales:
      • "Accept all" / "Reject all" buttons
      • "I agree" / "Agree" on older layouts
      • consent.google.com iframe
    """
    consent_labels = [
        "ok, got it", "got it", "accept all", "reject all", "i agree",
        "agree", "ok", "accept",
    ]
    # Try direct buttons first
    clicked = await _click_offer_button(page, consent_labels, timeout=2000)
    if clicked:
        logger.info("Dismissed cookie consent via '%s'", clicked)
        await page.wait_for_timeout(1000)
        return

    # Try consent.google.com iframe (common in EU)
    try:
        for frame in page.frames:
            if "consent.google.com" in (frame.url or ""):
                for label in consent_labels:
                    safe_label = label.replace("'", "\\'")
                    try:
                        btn = frame.locator(f"button:has-text('{safe_label}')")
                        if await btn.first.is_visible(timeout=1000):
                            await btn.first.click()
                            logger.info("Dismissed cookie consent (iframe) via '%s'", label)
                            await page.wait_for_timeout(1000)
                            return
                    except Exception:
                        continue
    except Exception:
        pass


async def _dismiss_google_one_prompts(page: Any) -> None:
    """Dismiss non-essential Google One prompts that block offer scanning."""
    # First: cookie consent banners (especially with EU proxies)
    await _dismiss_cookie_consent(page)

    for _ in range(3):
        clicked = await _click_offer_button(
            page,
            ["no thanks", "not now", "dismiss", "skip"],
            timeout=1500,
        )
        if not clicked:
            return
        await _wait_for_navigation(page, timeout=5000)


# ── marker lists ─────────────────────────────────────────────────────────────

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
    "your plan is active",
]

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
    "get started",
]

_GENERIC_OFFER_BUTTON_MARKERS = [
    "get started",
    "subscribe",
    "continue",
    "next",
]

_OFFER_FOLLOWUP_BUTTON_MARKERS = [
    "accept and continue",
    "i agree",
    "continue",
    "confirm",
    "confirm purchase",
    "subscribe",
    "next",
]

_PIXEL_OFFER_MARKERS = [
    "pixel",
    "giftbox",
    "gift box",
    "google ai pro",
    "gemini advanced",
    "google one ai premium",
    "ai premium",
]

_NOT_ELIGIBLE_MARKERS = [
    "not eligible",
    "isn't eligible",
    "not available",
    "offer has expired",
    "already redeemed",
    "can't redeem",
    "cannot redeem",
]

_SUCCESS_MARKERS = [
    "you're all set",
    "you are all set",
    "welcome to",
    "successfully",
    "subscription started",
    "enjoy your",
    "trial activated",
    "plan confirmed",
    "your trial",
    "activated",
    "congratulations",
]

_STRONG_SUCCESS_CONTEXT_MARKERS = [
    "google ai pro",
    "gemini advanced",
    "google one ai premium",
    "ai premium",
    "2 tb",
    "2tb",
    "google one",
]

# Google One offer/redeem URL candidates — ordered best-first.
# Both /u/0/ (account-indexed) and non-indexed variants are tried.
_OFFER_URLS = [
    "https://one.google.com/u/0/offers/redeem/pixel",
    "https://one.google.com/offers/redeem/pixel",
    "https://one.google.com/u/0/offers",
    "https://one.google.com/offers",
    "https://one.google.com/u/0/about/plans?hl=en",
    "https://one.google.com/about/plans?hl=en",
    "https://one.google.com/u/0/intl/en/about/plans",
    "https://one.google.com/intl/en/about/plans",
]


# ── offer page detection ──────────────────────────────────────────────────────

def _looks_like_offer_page(text: str, url: str) -> bool:
    lowered_url = url.lower()
    return (
        "offer" in lowered_url
        or "redeem" in lowered_url
        or "claim" in lowered_url
        or "gemini" in lowered_url
        or "google ai" in text
        or "ai premium" in text
        or "gemini advanced" in text
    )


def _has_offer_url_evidence(url: str) -> bool:
    lowered_url = url.lower()
    return any(token in lowered_url for token in ("offer", "redeem", "claim", "pixel", "gemini", "ai-premium"))


def _is_redeem_link_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
    except Exception:
        return False
    host = parsed.netloc.lower()
    target = f"{parsed.path.lower()}?{parsed.query.lower()}"
    if host != "one.google.com" and not host.endswith(".one.google.com"):
        return False
    if "/about/" in target or "/plans" in target or "/storage" in target or "/settings" in target:
        return False
    return any(token in target for token in ("offer", "offers", "redeem", "claim", "promo", "promotion"))


def _has_offer_context(text: str, url: str) -> bool:
    return _has_offer_url_evidence(url) or _check_markers(text, _PIXEL_OFFER_MARKERS)


def _has_non_generic_offer_button(text: str) -> bool:
    return any(
        marker in text
        for marker in _OFFER_BUTTON_MARKERS
        if marker not in _GENERIC_OFFER_BUTTON_MARKERS
    )


def _has_claimable_pixel_offer(text: str, url: str) -> bool:
    """True only when the page has a real claimable offer button."""
    if _looks_like_anonymous_google_one_page(text, url):
        return False
    if _check_markers(text, _NOT_ELIGIBLE_MARKERS):
        return False
    if not _has_non_generic_offer_button(text) and not (
        _has_offer_context(text, url) and _check_markers(text, _GENERIC_OFFER_BUTTON_MARKERS)
    ):
        return False
    return _has_offer_context(text, url)


def _has_strict_claim_success(text: str, url: str) -> bool:
    if _check_markers(text, _ALREADY_ACTIVE_MARKERS):
        return True
    if not _check_markers(text, _SUCCESS_MARKERS):
        return False
    return _has_offer_url_evidence(url) or _check_markers(text, _STRONG_SUCCESS_CONTEXT_MARKERS)


def _classify_offer_state(text: str, url: str) -> tuple[str, str]:
    """Classify a Google One page into a claim-flow state and reason."""
    text = text.lower()
    if _looks_like_anonymous_google_one_page(text, url):
        return OFFER_MANUAL_REQUIRED, "google_one_signin_required"
    if _check_markers(text, _ALREADY_ACTIVE_MARKERS):
        return OFFER_ALREADY_ACTIVE, "active_subscription_marker"
    if _check_markers(text, _PAYMENT_REQUIRED_MARKERS):
        return OFFER_PAYMENT_REQUIRED, "payment_method_required"
    if _check_markers(text, _NOT_ELIGIBLE_MARKERS):
        return OFFER_NOT_ELIGIBLE, "not_eligible_or_redeemed"
    if _has_strict_claim_success(text, url):
        return OFFER_CLAIMED, "strict_success_marker"
    if _has_claimable_pixel_offer(text, url):
        return OFFER_CLAIMABLE, "claimable_offer_detected"
    return OFFER_UNKNOWN, "no_offer_state_detected"


# ── offer link extraction ─────────────────────────────────────────────────────

def _score_offer_url(url: str) -> int:
    try:
        parsed = urlparse(url)
    except Exception:
        return 0
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
    if any(t in target for t in ("redeem", "claim", "promo", "promotion")):
        score += 25
    if any(t in target for t in ("gemini", "ai-premium", "google-ai", "pixel")):
        score += 20
    if any(t in target for t in ("checkout", "subscribe", "subscription", "purchase")):
        score += 10
    if target.rstrip("?") in ("", "/", "/settings", "/about/plans", "/plans", "/storage"):
        score -= 30
    if any(t in target for t in ("/plans", "/storage", "g1_last_touchpoint")):
        score -= 35

    return score


async def _extract_offer_link(page: Any, *preferred_urls: str) -> str:
    """Return the best Google One offer/redeem URL visible on the page.

    Collects links from:
      1. href attributes on <a> tags
      2. data-url / data-href attributes (Google uses these on SPAs)
      3. onclick / data-action strings that look like URLs
      4. Preferred URLs passed by caller
    """
    candidates: set[str] = set()
    base_url = page.url

    for url in (base_url, *preferred_urls):
        if url:
            candidates.add(urljoin(base_url, url))

    try:
        # Collect all link-like attributes in one JS call
        raw_links = await page.evaluate(r"""() => {
            const links = new Set();
            // <a href>
            document.querySelectorAll('a[href]').forEach(el => {
                const h = el.getAttribute('href');
                if (h) links.add(h);
            });
            // data-url, data-href, data-action
            document.querySelectorAll('[data-url],[data-href],[data-action]').forEach(el => {
                ['data-url','data-href','data-action'].forEach(attr => {
                    const v = el.getAttribute(attr);
                    if (v && (v.startsWith('/') || v.startsWith('http'))) links.add(v);
                });
            });
            // onclick="location.href='...'" or similar
            document.querySelectorAll('[onclick]').forEach(el => {
                const m = (el.getAttribute('onclick') || '').match(/['\"](\/[^'\"]+|https?:[^'\"]+)['\"]/)
                if (m) links.add(m[1]);
            });
            return [...links];
        }""")
        for href in (raw_links or []):
            if not isinstance(href, str):
                continue
            href = href.strip()
            if not href or href.startswith(("javascript:", "mailto:", "tel:")):
                continue
            candidates.add(urljoin(base_url, href))
    except Exception:
        pass

    ranked = sorted(
        ((_score_offer_url(url), url) for url in candidates if _is_redeem_link_url(url)),
        key=lambda item: item[0],
        reverse=True,
    )
    if ranked and ranked[0][0] >= 50:
        return ranked[0][1]
    return ""


# ── button clicking ───────────────────────────────────────────────────────────

async def _click_offer_button(page: Any, labels: list[str], timeout: int = 5000) -> str | None:
    """Click the first visible button whose text matches any label.

    Handles:
      • Standard HTML buttons / anchors
      • Google Material Web Components (mwc-button, material-button)
      • Shadow DOM elements (via JS pierce)
      • Elements with aria-label instead of visible text
      • Pre-click humanization (dwell + touch event)

    Returns the label that was clicked, or None.
    """
    # 1. Playwright locator approach (fastest)
    for label in labels:
        safe_label = label.replace("'", "\\'")
        selectors = [
            f"button:has-text('{safe_label}')",
            f"a:has-text('{safe_label}')",
            f"[role='button']:has-text('{safe_label}')",
            f"mwc-button:has-text('{safe_label}')",
            f"material-button:has-text('{safe_label}')",
            f"[jsname]:has-text('{safe_label}')",
            f"[aria-label*='{safe_label}' i]",
        ]
        combined = ", ".join(selectors)
        try:
            loc = page.locator(combined).first
            if await loc.is_visible(timeout=timeout):
                # Humanize: dwell + touch before clicking
                await _dwell_before_action(page)
                await _simulate_touch(page, combined.split(", ")[0])
                await loc.click(timeout=timeout)
                return label
        except Exception:
            pass

    # 2. JS fallback — pierces Shadow DOM and handles Web Components
    try:
        clicked_label = await page.evaluate("""(labels) => {
            function findAndClick(root) {
                for (const label of labels) {
                    const lower = label.toLowerCase();
                    const candidates = root.querySelectorAll(
                        'button, a, [role="button"], mwc-button, ' +
                        'material-button, [jsname], [tabindex="0"]'
                    );
                    for (const el of candidates) {
                        const txt = (
                            el.innerText || el.textContent ||
                            el.getAttribute('aria-label') || ''
                        ).trim().toLowerCase();
                        const style = window.getComputedStyle(el);
                        const hidden = (
                            style.display === 'none' ||
                            style.visibility === 'hidden' ||
                            el.offsetParent === null
                        );
                        if (!hidden && txt.includes(lower)) {
                            el.click();
                            return label;
                        }
                    }
                    // Check shadow roots one level deep
                    for (const el of root.querySelectorAll('*')) {
                        if (el.shadowRoot) {
                            const result = findAndClick(el.shadowRoot);
                            if (result) return result;
                        }
                    }
                }
                return null;
            }
            return findAndClick(document);
        }""", labels)
        if clicked_label:
            return clicked_label
    except Exception:
        pass

    return None


# ═══════════════════════════════════════════════════════════════════
#  MAIN CLAIM FUNCTION
# ═══════════════════════════════════════════════════════════════════

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

        # ── Step 1: Go to Google One home ─────────────────────────────
        await _goto_one(page, "/")
        await _screenshot(page, job_id, "08_one_home")
        if not await _ensure_google_one_authenticated(page, job_id):
            ss = await _screenshot(page, job_id, "08_google_one_still_anonymous")
            await _update_job_status(
                telegram_id, job_id, "PROCESSING",
                {
                    "offer_result": OFFER_MANUAL_REQUIRED,
                    "offer_reason": "google_one_signin_required",
                    "claim_result_url": page.url,
                    "progress_note": "Google One sign-in required",
                },
            )
            await _notify(
                bot,
                chat_id,
                f"âš ï¸ <b>Job {html_esc(job_id)}</b>\n\n"
                "<b>Google One sign-in required</b>\n"
                "Google One still shows the Sign in button, so offer scanning was stopped.",
            )
            if ss:
                await _notify_photo(bot, chat_id, ss, "Google One sign-in required")
            return OFFER_MANUAL_REQUIRED

        await _dismiss_google_one_prompts(page)
        await _screenshot(page, job_id, "08_one_authenticated")
        body = await _page_body(page)
        state, reason = _classify_offer_state(body, page.url)

        # Already subscribed?
        if state == OFFER_ALREADY_ACTIVE:
            ss = await _screenshot(page, job_id, "08_already_active")
            await _update_job_status(
                telegram_id, job_id, "PROCESSING",
                {"offer_result": state, "offer_reason": reason, "claim_result_url": page.url},
            )
            await _notify(
                bot, chat_id,
                f"ℹ️ <b>Job {html_esc(job_id)}</b>\n\n"
                "<b>Plan already active</b>\n"
                f"Account: <code>{html_esc(masked_email)}</code>",
            )
            if ss:
                await _notify_photo(bot, chat_id, ss, "Already subscribed")
            return OFFER_ALREADY_ACTIVE

        # ── Step 2: Try Settings → Offers ─────────────────────────────
        offer_found = False
        offer_page_url = ""
        initial_offer_link = ""
        not_eligible_url = ""
        not_eligible_reason = ""
        signin_required_url = ""
        signin_required_reason = ""

        try:
            await _goto_one(page, "/settings")
            await _dismiss_google_one_prompts(page)
            await _screenshot(page, job_id, "09_settings")
            for label in ["Check for offers", "Offers", "Promotions", "Redeem"]:
                try:
                    btn = page.locator(f"text={label}").first
                    if await btn.is_visible(timeout=3000):
                        await btn.click()
                        await _wait_for_navigation(page)
                        await page.wait_for_timeout(3000)
                        body = await _page_body(page)
                        state, reason = _classify_offer_state(body, page.url)
                        if state == OFFER_MANUAL_REQUIRED and reason == "google_one_signin_required":
                            signin_required_url = page.url
                            signin_required_reason = reason
                            break
                        if state == OFFER_NOT_ELIGIBLE:
                            not_eligible_url = page.url
                            not_eligible_reason = reason
                        if state == OFFER_CLAIMABLE:
                            offer_found = True
                            offer_page_url = page.url
                            logger.info("[%s] Offer found via Settings at: %s", job_id, page.url)
                            break
                except Exception:
                    continue
        except Exception:
            pass

        # ── Step 3: Try direct offer URLs ──────────────────────────────
        if not offer_found and not signin_required_url:
            for url_idx, url in enumerate(_OFFER_URLS):
                try:
                    try:
                        await page.goto(url, wait_until="domcontentloaded", timeout=25_000)
                    except Exception:
                        pass
                    # Dynamic settle — wait for SPA content
                    for _ in range(12):
                        await page.wait_for_timeout(500)
                        try:
                            body_len = await page.evaluate("document.body?.innerText?.length || 0")
                            if body_len > 50:
                                break
                        except Exception:
                            pass
                    await page.wait_for_timeout(1000)

                    await _dismiss_google_one_prompts(page)
                    # Per-URL debug screenshot
                    await _screenshot(page, job_id, f"09_offer_url_{url_idx}")

                    body = await _page_body(page)
                    state, reason = _classify_offer_state(body, page.url)
                    logger.info("[%s] URL #%d state=%s reason=%s url=%s", job_id, url_idx, state, reason, page.url)

                    if state == OFFER_MANUAL_REQUIRED and reason == "google_one_signin_required":
                        signin_required_url = page.url
                        signin_required_reason = reason
                        break

                    if state == OFFER_ALREADY_ACTIVE:
                        logger.info("[%s] Already subscribed at %s", job_id, url)
                        await _update_job_status(
                            telegram_id, job_id, "PROCESSING",
                            {"offer_result": state, "offer_reason": reason, "claim_result_url": page.url},
                        )
                        await _notify(
                            bot, chat_id,
                            f"ℹ️ <b>Job {html_esc(job_id)}</b>\n\n"
                            "<b>Plan already active</b>\n"
                            f"Account: <code>{html_esc(masked_email)}</code>",
                        )
                        return OFFER_ALREADY_ACTIVE

                    if state == OFFER_NOT_ELIGIBLE:
                        not_eligible_url = page.url
                        not_eligible_reason = reason
                        break

                    if state == OFFER_CLAIMABLE:
                        offer_found = True
                        offer_page_url = page.url
                        logger.info("[%s] Offer found at: %s", job_id, page.url)
                        break
                except Exception:
                    continue

        await _screenshot(page, job_id, "10_offer_page")

        # Last-resort: check wherever we landed
        if not offer_found:
            body = await _page_body(page)
            state, reason = _classify_offer_state(body, page.url)
            if state == OFFER_MANUAL_REQUIRED and reason == "google_one_signin_required":
                signin_required_url = page.url
                signin_required_reason = reason
            if state == OFFER_NOT_ELIGIBLE:
                not_eligible_url = page.url
                not_eligible_reason = reason
            if state == OFFER_CLAIMABLE:
                offer_found = True
                offer_page_url = page.url

        if not offer_found:
            if signin_required_url:
                ss = await _screenshot(page, job_id, "10_google_one_signin_required")
                await _update_job_status(
                    telegram_id, job_id, "PROCESSING",
                    {
                        "offer_result": OFFER_MANUAL_REQUIRED,
                        "offer_reason": signin_required_reason or "google_one_signin_required",
                        "claim_result_url": signin_required_url,
                        "progress_note": "Google One sign-in required",
                    },
                )
                await _notify(
                    bot,
                    chat_id,
                    f"<b>Job {html_esc(job_id)}</b>\n\n"
                    "<b>Google One sign-in required</b>\n"
                    "Google One opened the public plans page instead of the signed-in offer page.",
                )
                if ss:
                    await _notify_photo(bot, chat_id, ss, "Google One sign-in required")
                return OFFER_MANUAL_REQUIRED

            if not_eligible_url:
                ss = await _screenshot(page, job_id, "10_not_eligible")
                await _update_job_status(
                    telegram_id, job_id, "PROCESSING",
                    {
                        "offer_result": OFFER_NOT_ELIGIBLE,
                        "offer_reason": not_eligible_reason or "not_eligible_or_redeemed",
                        "claim_result_url": not_eligible_url,
                    },
                )
                await _notify(
                    bot, chat_id,
                    f"âš ï¸ <b>Job {html_esc(job_id)}</b>\n\n"
                    "<b>Offer not eligible</b>\n"
                    "Google says this account cannot redeem the Pixel/Gemini offer.",
                )
                if ss:
                    await _notify_photo(bot, chat_id, ss, "Offer not eligible")
                return OFFER_NOT_ELIGIBLE

            ss = await _screenshot(page, job_id, "10_no_offer")
            await _update_job_status(
                telegram_id, job_id, "PROCESSING",
                {
                    "offer_result": OFFER_NOT_FOUND,
                    "offer_reason": "no_claimable_offer_detected",
                    "claim_result_url": page.url,
                },
            )
            await _notify(
                bot, chat_id,
                f"⚠️ <b>Job {html_esc(job_id)}</b>\n\n"
                "<b>No eligible offer found</b>\n"
                "This account does not currently show a Pixel/Gemini offer.",
            )
            if ss:
                await _notify_photo(bot, chat_id, ss, "No offer found")
            return OFFER_NOT_FOUND

        # ── Step 4: Extract offer link and store it ────────────────────
        offer_page_url = offer_page_url or page.url
        initial_offer_link = await _extract_offer_link(page, offer_page_url)
        status_extra: dict[str, str] = {
            "offer_result": OFFER_CLAIMABLE,
            "offer_reason": "claimable_offer_detected",
            "offer_page_url": offer_page_url,
        }
        if initial_offer_link:
            status_extra["redeem_link"] = initial_offer_link
        await _update_job_status(telegram_id, job_id, "PROCESSING", status_extra)

        await _notify(
            bot, chat_id,
            f"🎁 <b>Job {html_esc(job_id)}</b>\n\n"
            "<b>Offer found!</b>\n"
            "Starting the claim flow."
            + (f"\n\n🔗 Offer link: {initial_offer_link}" if initial_offer_link else ""),
        )

        # ── Step 5: Click through claim flow ──────────────────────────
        terminal_state = ""
        terminal_reason = ""
        clicked_labels: list[str] = []

        for step in range(6):   # up to 6 button screens
            body = await _page_body(page)

            # Guard: if we somehow landed on the anonymous page, stop
            if _looks_like_anonymous_google_one_page(body, page.url):
                logger.warning("[%s] step=%d landed on anonymous page, aborting", job_id, step)
                break

            # Build button label list for this step
            labels = list(_OFFER_BUTTON_MARKERS)
            if _looks_like_offer_page(body, page.url):
                labels.extend(_OFFER_FOLLOWUP_BUTTON_MARKERS)

            # Scroll down slightly before clicking (real users scroll to see buttons)
            await _human_scroll(page, "down")
            await _dwell_before_action(page)

            clicked_label = await _click_offer_button(page, labels, timeout=4000)
            if clicked_label:
                clicked_labels.append(clicked_label)
                logger.info("[%s] step=%d clicked '%s'", job_id, step, clicked_label)
                await _wait_for_navigation(page)
                await page.wait_for_timeout(3000)
                await _screenshot(page, job_id, f"11_claim_step_{step}")
            else:
                # No clickable button found — we're done or stuck
                logger.info("[%s] step=%d no button found, stopping", job_id, step)
                break

            # Check success after each click
            body = await _page_body(page)
            state, reason = _classify_offer_state(body, page.url)
            if state in {OFFER_CLAIMED, OFFER_ALREADY_ACTIVE, OFFER_NOT_ELIGIBLE}:
                terminal_state = state
                terminal_reason = reason
                break
            if state == OFFER_PAYMENT_REQUIRED:
                terminal_state = state
                terminal_reason = reason
                await _update_job_status(
                    telegram_id, job_id, "PROCESSING",
                    {
                        "offer_result": state,
                        "offer_reason": reason,
                        "claim_result_url": page.url,
                        "offer_page_url": offer_page_url,
                        "clicked_offer_buttons": ", ".join(clicked_labels),
                        "progress_note": "Payment method required",
                    },
                )
                break

        ss = await _screenshot(page, job_id, "12_claim_result")

        # ── Step 6: Evaluate final state ───────────────────────────────
        body = await _page_body(page)
        redeem_link = await _extract_offer_link(
            page, page.url, offer_page_url, initial_offer_link,
        )
        claim_extra: dict[str, str] = {
            "claim_result_url": page.url,
            "offer_page_url": offer_page_url,
        }
        if redeem_link:
            claim_extra["redeem_link"] = redeem_link
        if clicked_labels:
            claim_extra["clicked_offer_buttons"] = ", ".join(clicked_labels)

        final_state, final_reason = _classify_offer_state(body, page.url)
        if terminal_state:
            final_state, final_reason = terminal_state, terminal_reason
        claim_extra["offer_result"] = final_state
        claim_extra["offer_reason"] = final_reason

        if final_state in {OFFER_CLAIMED, OFFER_ALREADY_ACTIVE}:
            await _update_job_status(telegram_id, job_id, "PROCESSING", claim_extra)
            await _notify(
                bot, chat_id,
                f"🎉 <b>Job {html_esc(job_id)}</b>\n\n"
                "<b>Offer claimed successfully!</b>\n"
                f"Account: <code>{html_esc(masked_email)}</code>\n"
                "Google One AI Premium / Gemini Advanced activated."
                + (f"\n\n🔗 Redeem link: {redeem_link}" if redeem_link else ""),
            )
            if ss:
                await _notify_photo(bot, chat_id, ss, "🎉 Offer claimed!")
            return OFFER_CLAIMED if final_state == OFFER_CLAIMED else OFFER_ALREADY_ACTIVE

        if final_state == OFFER_NOT_ELIGIBLE:
            claim_extra["progress_note"] = "Offer not eligible"
            await _update_job_status(telegram_id, job_id, "PROCESSING", claim_extra)
            await _notify(
                bot, chat_id,
                f"âš ï¸ <b>Job {html_esc(job_id)}</b>\n\n"
                "<b>Offer not eligible</b>\n"
                "Google says this account cannot redeem the Pixel/Gemini offer."
                + (f"\n\nðŸ”— Offer link: {redeem_link}" if redeem_link else ""),
            )
            if ss:
                await _notify_photo(bot, chat_id, ss, "Offer not eligible")
            return OFFER_NOT_ELIGIBLE

        # Offer found but couldn't fully complete
        needs_payment = final_state == OFFER_PAYMENT_REQUIRED
        result = OFFER_PAYMENT_REQUIRED if needs_payment else OFFER_MANUAL_REQUIRED
        claim_extra["offer_result"] = result
        claim_extra["progress_note"] = (
            "Payment method required" if needs_payment
            else "Claim requires manual completion"
        )
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
            await _notify_photo(bot, chat_id, ss, "Payment required" if needs_payment else "Claim incomplete")
        return result

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
        return OFFER_CLAIM_FAILED
