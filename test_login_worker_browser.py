import bot.login_worker.browser as browser
from bot.login_worker.config import (
    ANDROID_DPR,
    ANDROID_SCREEN,
    ANDROID_USER_AGENT,
    ANDROID_VIEWPORT,
    CLIENT_HINTS_HEADERS,
    PIXEL_10_PRO_PROFILE,
)
import unittest


class ResourceBlockingTests(unittest.TestCase):
    def test_blocks_configured_heavy_resource_types(self) -> None:
        self.assertTrue(browser._should_block_request("https://example.com/logo.png", "image"))
        self.assertTrue(browser._should_block_request("https://example.com/clip.mp4", "media"))
        self.assertTrue(browser._should_block_request("https://example.com/font.woff2", "font"))

    def test_allows_core_google_page_resources(self) -> None:
        self.assertFalse(browser._should_block_request("https://accounts.google.com/", "document"))
        self.assertFalse(browser._should_block_request("https://accounts.google.com/app.js", "script"))
        self.assertFalse(
            browser._should_block_request("https://accounts.google.com/styles.css", "stylesheet")
        )
        self.assertFalse(browser._should_block_request("https://accounts.google.com/api", "xhr"))
        self.assertFalse(browser._should_block_request("https://one.google.com/_/fetch", "fetch"))

    def test_blocks_tracker_urls_even_when_resource_type_is_allowed(self) -> None:
        self.assertTrue(browser._should_block_request("https://www.googletagmanager.com/gtm.js", "script"))


class AndroidProfileTests(unittest.TestCase):
    def test_login_worker_android_constants_come_from_shared_profile(self) -> None:
        self.assertEqual(ANDROID_VIEWPORT, PIXEL_10_PRO_PROFILE.viewport)
        self.assertEqual(ANDROID_SCREEN, PIXEL_10_PRO_PROFILE.screen)
        self.assertEqual(ANDROID_DPR, PIXEL_10_PRO_PROFILE.device_scale_factor)
        self.assertEqual(CLIENT_HINTS_HEADERS, PIXEL_10_PRO_PROFILE.extra_http_headers)
        self.assertIn("Pixel 10 Pro", ANDROID_USER_AGENT)
        self.assertIn("Android 16", ANDROID_USER_AGENT)


class BrowserLaunchTests(unittest.IsolatedAsyncioTestCase):
    def test_android_context_uses_matching_chromium_version(self) -> None:
        kwargs = browser._build_android_context_kwargs("147.0.7727.15")

        self.assertIn("Chrome/147.0.7727.15", kwargs["user_agent"])
        self.assertEqual(PIXEL_10_PRO_PROFILE.screen, kwargs["screen"])
        self.assertIn("Pixel 10 Pro", kwargs["user_agent"])
        self.assertIn("Android 16", kwargs["user_agent"])
        self.assertIn('"Chromium";v="147"', kwargs["extra_http_headers"]["sec-ch-ua"])
        self.assertEqual('"Pixel 10 Pro"', kwargs["extra_http_headers"]["sec-ch-ua-model"])
        self.assertIn(
            '"Google Chrome";v="147.0.7727.15"',
            kwargs["extra_http_headers"]["sec-ch-ua-full-version-list"],
        )
        self.assertTrue(kwargs["is_mobile"])
        self.assertTrue(kwargs["has_touch"])

    def test_build_playwright_proxy_skips_direct(self) -> None:
        self.assertIsNone(browser._build_playwright_proxy(None))
        self.assertIsNone(browser._build_playwright_proxy({"server": "direct://"}))

    def test_build_playwright_proxy_preserves_auth(self) -> None:
        proxy = {
            "server": "http://proxy.example.com:8080",
            "username": "user",
            "password": "pass",
        }

        self.assertEqual(browser._build_playwright_proxy(proxy), proxy)

    async def test_launch_android_browser_uses_runtime_chromium_version(self) -> None:
        original_async_playwright = browser.async_playwright
        captured = {}

        class FakeContext:
            async def add_init_script(self, script):
                captured["init_script"] = script

        class FakeBrowser:
            version = "147.0.7727.15"

            async def new_context(self, **kwargs):
                captured["context_kwargs"] = kwargs
                return FakeContext()

            async def close(self):
                captured["closed"] = True

        class FakeChromium:
            async def launch(self, **kwargs):
                captured["launch_kwargs"] = kwargs
                return FakeBrowser()

        class FakePlaywright:
            chromium = FakeChromium()

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return None

        def fake_async_playwright():
            return FakePlaywright()

        try:
            browser.async_playwright = fake_async_playwright
            async with browser._launch_android_browser({"server": "direct://"}) as launched:
                context = await launched.new_context()
                self.assertIsNotNone(context)
        finally:
            browser.async_playwright = original_async_playwright

        context_kwargs = captured["context_kwargs"]
        self.assertIn("Chrome/147.0.7727.15", context_kwargs["user_agent"])
        self.assertIn("Pixel 10 Pro", context_kwargs["user_agent"])
        self.assertIsNone(captured["launch_kwargs"]["proxy"])
        self.assertIn("navigator, 'webdriver'", captured["init_script"])
        self.assertTrue(captured["closed"])


if __name__ == "__main__":
    unittest.main()
