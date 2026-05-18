"""Test direct (no proxy) Google login page access via Firefox."""
import asyncio
import time

from invisible_playwright.async_api import InvisiblePlaywright

extra_prefs = {
    "network.stricttransportsecurity.preloadlist": False,
    "security.cert_pinning.enforcement_level": 0,
}


async def main():
    print("Testing direct connection (no proxy) to Google login...")
    start = time.time()
    async with InvisiblePlaywright(extra_args=["-min"], extra_prefs=extra_prefs) as browser:
        ctx = await browser.new_context()
        page = await ctx.new_page()
        page.set_default_navigation_timeout(30000)
        try:
            url = "https://accounts.google.com/signin/v2/identifier?flowName=GlifWebSignIn&flowEntry=ServiceLogin"
            await page.goto(url, wait_until="domcontentloaded")
            title = await page.title()
            text = (await page.inner_text("body"))[:150]
            print(f"OK  SUCCESS in {time.time()-start:.1f}s")
            print(f"    Title: {title}")
            print(f"    Body:  {text[:100]}")
        except Exception as e:
            print(f"ERR FAILED in {time.time()-start:.1f}s")
            print(f"    {type(e).__name__}: {str(e)[:300]}")
        await ctx.close()


asyncio.run(main())
