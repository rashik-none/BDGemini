"""Test SOCKS5 proxy via Playwright's own proxy= kwarg (not Firefox prefs)."""
import asyncio
import time

from invisible_playwright.async_api import InvisiblePlaywright


proxy = {
    "server": "socks5://brd.superproxy.io:22225",
    "username": "brd-customer-hl_fc20e5ef-zone-residential_proxy1",
    "password": "diza2wz9o9bp",
}

extra_prefs = {
    "network.stricttransportsecurity.preloadlist": False,
    "security.cert_pinning.enforcement_level": 0,
    "security.mixed_content.block_active_content": False,
    "security.mixed_content.block_display_content": False,
    "security.enterprise_roots.enabled": True,
}


async def main():
    print("Launching Firefox with SOCKS5 via Playwright proxy= kwarg...")
    start = time.time()
    async with InvisiblePlaywright(
        proxy=proxy,
        extra_args=["-min"],
        extra_prefs=extra_prefs,
    ) as browser:
        ctx = await browser.new_context()
        page = await ctx.new_page()
        page.set_default_navigation_timeout(30000)
        try:
            await page.goto("https://ipinfo.io/json", wait_until="domcontentloaded")
            text = await page.inner_text("body")
            print(f"SUCCESS in {time.time()-start:.1f}s")
            print(text[:300])
        except Exception as e:
            print(f"FAILED in {time.time()-start:.1f}s => {type(e).__name__}: {str(e)[:300]}")
        await ctx.close()


asyncio.run(main())
