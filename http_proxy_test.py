"""Test HTTP proxy port 33335 with Firefox — CA cert now installed in Windows store."""
import asyncio
import time

from invisible_playwright.async_api import InvisiblePlaywright

proxy = {
    "server": "http://brd.superproxy.io:33335",
    "username": "brd-customer-hl_fc20e5ef-zone-residential_proxy1",
    "password": "diza2wz9o9bp",
}

extra_prefs = {
    "network.stricttransportsecurity.preloadlist": False,
    "security.cert_pinning.enforcement_level": 0,
    "security.mixed_content.block_active_content": False,
    "security.mixed_content.block_display_content": False,
    # Trust Windows cert store — picks up the Bright Data CA we just installed
    "security.enterprise_roots.enabled": True,
}


async def main():
    print("Testing HTTP proxy port 33335 with Firefox (Bright Data CA installed)...")
    start = time.time()
    async with InvisiblePlaywright(
        proxy=proxy,
        extra_args=["-min"],
        extra_prefs=extra_prefs,
    ) as browser:
        ctx = await browser.new_context()
        page = await ctx.new_page()
        page.set_default_navigation_timeout(30000)
        for url in ["https://ipinfo.io/json", "https://accounts.google.com/"]:
            try:
                await page.goto(url, wait_until="domcontentloaded")
                text = (await page.inner_text("body"))[:200]
                print(f"OK  SUCCESS {url} in {time.time()-start:.1f}s")
                print(f"   {text[:120]}")
            except Exception as e:
                print(f"ERR FAILED  {url} in {time.time()-start:.1f}s")
                print(f"   {type(e).__name__}: {str(e)[:200]}")
        await ctx.close()


asyncio.run(main())
