import bot.login_worker.browser as browser
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


if __name__ == "__main__":
    unittest.main()
