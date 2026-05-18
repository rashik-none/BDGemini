import bot.login_worker.browser as browser
from bot.login_worker.config import (
    ANDROID_DPR,
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
        self.assertEqual(ANDROID_USER_AGENT, PIXEL_10_PRO_PROFILE.user_agent)
        self.assertEqual(ANDROID_VIEWPORT, PIXEL_10_PRO_PROFILE.viewport)
        self.assertEqual(ANDROID_DPR, PIXEL_10_PRO_PROFILE.device_scale_factor)
        self.assertEqual(CLIENT_HINTS_HEADERS, PIXEL_10_PRO_PROFILE.extra_http_headers)


class BrowserLaunchTests(unittest.IsolatedAsyncioTestCase):
    async def test_launch_android_browser_uses_pixel_device_profile(self) -> None:
        original_cls = browser.InvisiblePlaywright
        captured = {}

        class FakeInvisiblePlaywright:
            def __init__(self, **kwargs):
                captured.update(kwargs)

            async def __aenter__(self):
                return object()

            async def __aexit__(self, *exc):
                return None

        try:
            browser.InvisiblePlaywright = FakeInvisiblePlaywright
            async with browser._launch_android_browser(None) as launched:
                self.assertIsNotNone(launched)
        finally:
            browser.InvisiblePlaywright = original_cls

        self.assertEqual(captured["device_profile"], "pixel_10_pro")
        self.assertIsNone(captured["proxy"])


if __name__ == "__main__":
    unittest.main()
