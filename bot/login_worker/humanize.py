"""Behavioral humanization helpers.

Dispatches realistic touch, scroll, and pointer events to reduce
bot risk score from Google's AI-based detection systems.

A real Android user:
  • Touches and drags before tapping a button
  • Scrolls the page slightly while reading
  • Pauses between actions with variable timing
  • Doesn't jump instantly to form fields

These helpers simulate that behaviour inside Playwright.
"""

from __future__ import annotations

import logging
import random
from typing import Any

logger = logging.getLogger(__name__)


# ── Touch event simulation ──────────────────────────────────────────────────

async def _simulate_touch(page: Any, selector: str) -> None:
    """Dispatch a realistic touchstart → touchmove → touchend sequence on *selector*.

    Why this matters:
      Google checks the ``ontouchstart`` event log.  If ``has_touch: true``
      is set but zero ``TouchEvent`` objects are ever dispatched, the
      session is flagged as a desktop automation pretending to be mobile.

    The sequence mimics a real finger tap with slight jitter (±3 px) and
    a brief hold time (80-200 ms).
    """
    try:
        box = await page.evaluate("""
            (sel) => {
                const el = document.querySelector(sel);
                if (!el) return null;
                const r = el.getBoundingClientRect();
                return { x: r.x + r.width / 2, y: r.y + r.height / 2 };
            }
        """, selector)
        if not box:
            return

        # Slight jitter — real fingers never hit the exact center
        cx = box["x"] + random.uniform(-3, 3)
        cy = box["y"] + random.uniform(-3, 3)

        # 1) touchstart
        await page.evaluate("""
            ([x, y]) => {
                const touch = new Touch({
                    identifier: 0,
                    target: document.elementFromPoint(x, y) || document.body,
                    clientX: x, clientY: y,
                    pageX: x, pageY: y,
                });
                const evt = new TouchEvent('touchstart', {
                    touches: [touch], targetTouches: [touch],
                    changedTouches: [touch], bubbles: true, cancelable: true,
                });
                (document.elementFromPoint(x, y) || document.body).dispatchEvent(evt);
            }
        """, [cx, cy])

        # 2) small touchmove jitter (finger slides ~1-2px)
        await page.wait_for_timeout(random.randint(30, 80))
        jx = cx + random.uniform(-2, 2)
        jy = cy + random.uniform(-2, 2)
        await page.evaluate("""
            ([x, y]) => {
                const touch = new Touch({
                    identifier: 0,
                    target: document.elementFromPoint(x, y) || document.body,
                    clientX: x, clientY: y,
                    pageX: x, pageY: y,
                });
                const evt = new TouchEvent('touchmove', {
                    touches: [touch], targetTouches: [touch],
                    changedTouches: [touch], bubbles: true, cancelable: true,
                });
                (document.elementFromPoint(x, y) || document.body).dispatchEvent(evt);
            }
        """, [jx, jy])

        # 3) brief hold
        await page.wait_for_timeout(random.randint(80, 200))

        # 4) touchend
        await page.evaluate("""
            ([x, y]) => {
                const touch = new Touch({
                    identifier: 0,
                    target: document.elementFromPoint(x, y) || document.body,
                    clientX: x, clientY: y,
                    pageX: x, pageY: y,
                });
                const evt = new TouchEvent('touchend', {
                    touches: [], targetTouches: [],
                    changedTouches: [touch], bubbles: true, cancelable: true,
                });
                (document.elementFromPoint(x, y) || document.body).dispatchEvent(evt);
            }
        """, [cx, cy])
    except Exception:
        # Touch simulation is best-effort — never crash the flow
        logger.debug("Touch simulation failed for %s", selector, exc_info=True)


# ── Scroll simulation ───────────────────────────────────────────────────────

async def _human_scroll(page: Any, direction: str = "down") -> None:
    """Simulate a natural mobile scroll gesture.

    Real users scroll slightly while reading a page.  Zero scroll events
    across an entire session is a strong bot signal.

    Args:
        page: Playwright page object.
        direction: "down" (default) or "up".
    """
    try:
        distance = random.randint(80, 280)
        if direction == "up":
            distance = -distance

        # Use smooth scroll via JS — more realistic than Playwright's mouse.wheel
        await page.evaluate("""
            (dist) => {
                window.scrollBy({ top: dist, behavior: 'smooth' });
            }
        """, distance)
        # Wait for scroll to complete visually
        await page.wait_for_timeout(random.randint(400, 900))
    except Exception:
        logger.debug("Scroll simulation failed", exc_info=True)


async def _human_scroll_to_element(page: Any, selector: str) -> None:
    """Scroll an element into view with a human-like smooth scroll.

    Unlike Playwright's built-in scrollIntoView (instant), this uses
    smooth scrolling which generates realistic scroll events.
    """
    try:
        await page.evaluate("""
            (sel) => {
                const el = document.querySelector(sel);
                if (el) {
                    el.scrollIntoView({ behavior: 'smooth', block: 'center' });
                }
            }
        """, selector)
        await page.wait_for_timeout(random.randint(300, 700))
    except Exception:
        logger.debug("Scroll-to-element failed for %s", selector, exc_info=True)


# ── Viewport exploration ─────────────────────────────────────────────────────

async def _explore_viewport(page: Any) -> None:
    """Mimic a user briefly looking around the page before acting.

    Real users don't immediately interact — they glance at the page
    content, scroll slightly, and then focus on the input fields.
    This creates natural scroll + timing patterns that lower bot risk.
    """
    try:
        # Small scroll down — "reading" the page
        await _human_scroll(page, "down")
        await page.wait_for_timeout(random.randint(500, 1500))

        # Scroll back up to the form area
        await _human_scroll(page, "up")
        await page.wait_for_timeout(random.randint(300, 800))

        # Random short touch on body (like accidentally touching the screen)
        if random.random() < 0.3:  # 30% chance
            await page.evaluate("""
                () => {
                    const x = 100 + Math.random() * 200;
                    const y = 200 + Math.random() * 400;
                    const touch = new Touch({
                        identifier: 0,
                        target: document.elementFromPoint(x, y) || document.body,
                        clientX: x, clientY: y,
                        pageX: x, pageY: y,
                    });
                    document.body.dispatchEvent(new TouchEvent('touchstart', {
                        touches: [touch], targetTouches: [touch],
                        changedTouches: [touch], bubbles: true,
                    }));
                    document.body.dispatchEvent(new TouchEvent('touchend', {
                        touches: [], targetTouches: [],
                        changedTouches: [touch], bubbles: true,
                    }));
                }
            """)
            await page.wait_for_timeout(random.randint(200, 500))
    except Exception:
        logger.debug("Viewport exploration failed", exc_info=True)


# ── Pre-click dwell ──────────────────────────────────────────────────────────

async def _dwell_before_action(page: Any) -> None:
    """Add a brief human-like pause before interacting with an element.

    Real users don't click the instant an element becomes visible.
    There's always a perception-reaction delay (200-800ms).
    """
    await page.wait_for_timeout(random.randint(200, 800))
