"""Test regular Playwright Firefox (non-invisible) navigation to Google."""
import asyncio
from playwright.async_api import async_playwright

async def main():
    print("Launching regular Playwright Firefox...")
    async with async_playwright() as p:
        browser = await p.firefox.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()
        page.set_default_navigation_timeout(30000)
        print("Navigating to accounts.google.com ...")
        try:
            resp = await page.goto(
                "https://accounts.google.com/signin/v2/identifier?flowName=GlifWebSignIn&flowEntry=ServiceLogin&continue=https://one.google.com/&hl=en",
                wait_until="commit",
                timeout=30000,
            )
            print(f"Response status: {resp.status if resp else 'N/A'}")
            await page.wait_for_timeout(3000)
            print(f"Final URL: {page.url}")
            title = await page.title()
            print(f"Page title: {title}")
            email_visible = await page.locator('input[type="email"]').first.is_visible()
            print(f"Email input visible: {email_visible}")
            await page.screenshot(path="screenshots/plain_pw_test.png")
            print("Screenshot saved: screenshots/plain_pw_test.png")
        except Exception as e:
            print(f"ERROR: {type(e).__name__}: {e}")
            try:
                await page.screenshot(path="screenshots/plain_pw_error.png")
                print("Error screenshot saved.")
            except Exception:
                pass
        await browser.close()
    print("Done.")

asyncio.run(main())
