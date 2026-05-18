import unittest

from playwright.async_api import TimeoutError as PlaywrightTimeoutError

import bot.login_worker.google_login as google_login


class _FakeLocator:
    def __init__(self, visible: bool) -> None:
        self.first = self
        self._visible = visible

    async def is_visible(self, timeout: int = 0) -> bool:
        return self._visible

    def or_(self, other: "_FakeLocator") -> "_FakeLocator":
        return _FakeLocator(self._visible or other._visible)

    async def wait_for(self, state: str, timeout: int) -> None:
        if not self._visible:
            raise PlaywrightTimeoutError("locator timed out")


class _FakeLoginPage:
    url = "https://accounts.google.com/"

    def __init__(self, email_visible: bool = False) -> None:
        self.email_visible = email_visible
        self.goto_calls = 0
        self.wait_calls = 0

    async def goto(self, *args, **kwargs):
        self.goto_calls += 1
        raise PlaywrightTimeoutError("navigation timed out")

    async def inner_text(self, selector: str) -> str:
        return ""

    def locator(self, selector: str) -> _FakeLocator:
        return _FakeLocator(selector in {'input[type="email"]', "#identifierId"} and self.email_visible)

    async def wait_for_timeout(self, timeout: int) -> None:
        self.wait_calls += 1


class _FakeNavigationWaitPage:
    def __init__(self, raise_on_load: bool = False) -> None:
        self.raise_on_load = raise_on_load
        self.load_states = []
        self.timeouts = []

    async def wait_for_load_state(self, state: str, timeout: int) -> None:
        self.load_states.append((state, timeout))
        if self.raise_on_load:
            raise PlaywrightTimeoutError("load state timed out")

    async def wait_for_timeout(self, timeout: int) -> None:
        self.timeouts.append(timeout)


class GoogleLoginNavigationTests(unittest.IsolatedAsyncioTestCase):
    async def test_wait_for_navigation_uses_domcontentloaded_not_networkidle(self) -> None:
        page = _FakeNavigationWaitPage()

        await google_login._wait_for_navigation(page)

        self.assertEqual(page.load_states, [("domcontentloaded", 5000)])
        self.assertNotIn(("networkidle", 5000), page.load_states)
        self.assertEqual(page.timeouts, [google_login.POST_ACTION_SETTLE_MS])

    async def test_wait_for_navigation_still_settles_after_load_timeout(self) -> None:
        page = _FakeNavigationWaitPage(raise_on_load=True)

        await google_login._wait_for_navigation(page)

        self.assertEqual(page.load_states, [("domcontentloaded", 5000)])
        self.assertEqual(page.timeouts, [google_login.POST_ACTION_SETTLE_MS])

    def test_google_login_url_is_well_formed(self) -> None:
        url = google_login._google_login_url()

        self.assertEqual(url, "https://one.google.com/")

    async def test_goto_google_login_accepts_rendered_email_page_after_timeout(self) -> None:
        page = _FakeLoginPage(email_visible=True)

        await google_login._goto_google_login(page)

        self.assertEqual(page.goto_calls, 1)
        self.assertEqual(page.wait_calls, 0)

    async def test_goto_google_login_retries_when_page_state_is_unknown(self) -> None:
        page = _FakeLoginPage(email_visible=False)

        with self.assertRaises(PlaywrightTimeoutError):
            await google_login._goto_google_login(page, attempts=2)

        self.assertEqual(page.goto_calls, 2)
        self.assertEqual(page.wait_calls, 1)


if __name__ == "__main__":
    unittest.main()
