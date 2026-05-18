import unittest

import bot.login_worker.offer as offer


class _FakeLinkPage:
    url = "https://one.google.com/offers"

    async def evaluate(self, script):
        return [
            "/settings",
            "/offers/redeem/pixel",
            "https://one.google.com/about/plans",
            "https://example.com/offers/redeem/pixel",
            "/checkout?promo=gemini",
        ]


class _FakeMarketingOnlyPage:
    url = "https://one.google.com/about/about/ai-premium"

    async def evaluate(self, script):
        return [
            "https://one.google.com/about/about/ai-premium",
            "https://one.google.com/about/plans?hl=en",
        ]


class _FakeVisibleLocator:
    first = None

    def __init__(self, visible: bool) -> None:
        self.first = self
        self.visible = visible

    async def is_visible(self, timeout: int = 0) -> bool:
        return self.visible


class _FakeSigninPage:
    def __init__(self, visible: bool) -> None:
        self.visible = visible

    def locator(self, selector: str) -> _FakeVisibleLocator:
        return _FakeVisibleLocator(self.visible and "Sign in" in selector)


class _FakeMultiLocator:
    first = None

    def __init__(self, visible_values: list[bool]) -> None:
        self.first = _FakeVisibleLocator(visible_values[0])
        self.visible_values = visible_values

    async def count(self) -> int:
        return len(self.visible_values)

    def nth(self, index: int) -> _FakeVisibleLocator:
        return _FakeVisibleLocator(self.visible_values[index])


class _FakeMultiSigninPage:
    def locator(self, selector: str) -> _FakeMultiLocator:
        if "Sign in" not in selector:
            return _FakeMultiLocator([False])
        return _FakeMultiLocator([False, True])


class OfferClassifierTests(unittest.TestCase):
    def test_claimable_pixel_offer_is_detected(self) -> None:
        text = "Pixel offer: get Gemini Advanced. Claim offer now."
        state, reason = offer._classify_offer_state(text.lower(), "https://one.google.com/offers/redeem/pixel")

        self.assertEqual(state, offer.OFFER_CLAIMABLE)
        self.assertEqual(reason, "claimable_offer_detected")
        self.assertTrue(offer._has_claimable_pixel_offer(text.lower(), "https://one.google.com/offers/redeem/pixel"))

    def test_generic_plans_page_is_not_claimable(self) -> None:
        text = "Google One storage plans. Basic 100 GB. Get started."

        state, _ = offer._classify_offer_state(text.lower(), "https://one.google.com/about/plans")

        self.assertEqual(state, offer.OFFER_UNKNOWN)
        self.assertFalse(offer._has_claimable_pixel_offer(text.lower(), "https://one.google.com/about/plans"))

    def test_anonymous_plans_page_is_signin_required_not_claimable(self) -> None:
        text = (
            "Sign in. Choose the Google One plan that's right for you. "
            "All Google accounts come with up to 15 GB of storage. "
            "Google AI Premium. Get started."
        )

        state, reason = offer._classify_offer_state(text.lower(), "https://one.google.com/about/plans?hl=en")

        self.assertEqual(state, offer.OFFER_MANUAL_REQUIRED)
        self.assertEqual(reason, "google_one_signin_required")
        self.assertFalse(offer._has_claimable_pixel_offer(text.lower(), "https://one.google.com/about/plans?hl=en"))

    def test_not_eligible_wins_over_claim_button(self) -> None:
        text = "Claim offer. This offer has expired and cannot redeem."

        state, reason = offer._classify_offer_state(text.lower(), "https://one.google.com/offers/redeem/pixel")

        self.assertEqual(state, offer.OFFER_NOT_ELIGIBLE)
        self.assertEqual(reason, "not_eligible_or_redeemed")
        self.assertFalse(offer._has_claimable_pixel_offer(text.lower(), "https://one.google.com/offers/redeem/pixel"))

    def test_payment_required_is_classified(self) -> None:
        text = "Add a payment method to continue checkout and subscribe."

        state, reason = offer._classify_offer_state(text.lower(), "https://one.google.com/checkout")

        self.assertEqual(state, offer.OFFER_PAYMENT_REQUIRED)
        self.assertEqual(reason, "payment_method_required")

    def test_success_marker_alone_is_not_claimed(self) -> None:
        text = "You're all set."

        state, _ = offer._classify_offer_state(text.lower(), "https://one.google.com/")

        self.assertEqual(state, offer.OFFER_UNKNOWN)

    def test_success_marker_with_ai_context_is_claimed(self) -> None:
        text = "You're all set. Google One AI Premium is active."

        state, reason = offer._classify_offer_state(text.lower(), "https://one.google.com/")

        self.assertEqual(state, offer.OFFER_CLAIMED)
        self.assertEqual(reason, "strict_success_marker")

    def test_active_plan_marker_is_already_active(self) -> None:
        text = "Manage subscription. Current plan Google One AI Premium."

        state, reason = offer._classify_offer_state(text.lower(), "https://one.google.com/")

        self.assertEqual(state, offer.OFFER_ALREADY_ACTIVE)
        self.assertEqual(reason, "active_subscription_marker")


class OfferUrlTests(unittest.IsolatedAsyncioTestCase):
    def test_score_prefers_redeem_pixel_offer_over_generic_pages(self) -> None:
        redeem_score = offer._score_offer_url("https://one.google.com/offers/redeem/pixel")
        settings_score = offer._score_offer_url("https://one.google.com/settings")
        plans_score = offer._score_offer_url("https://one.google.com/about/plans")
        storage_score = offer._score_offer_url("https://one.google.com/storage")

        self.assertGreater(redeem_score, settings_score)
        self.assertGreater(redeem_score, plans_score)
        self.assertGreater(redeem_score, storage_score)

    def test_redeem_link_excludes_marketing_ai_premium_url(self) -> None:
        self.assertTrue(offer._is_redeem_link_url("https://one.google.com/offer/0N8GPG6ECMWULFKG8FG1?hl=en"))
        self.assertFalse(offer._is_redeem_link_url("https://one.google.com/about/about/ai-premium"))
        self.assertFalse(offer._is_redeem_link_url("https://one.google.com/about/plans?hl=en"))

    async def test_extract_offer_link_handles_link_like_attributes(self) -> None:
        page = _FakeLinkPage()

        result = await offer._extract_offer_link(page)

        self.assertEqual(result, "https://one.google.com/offers/redeem/pixel")

    async def test_extract_offer_link_ignores_marketing_only_links(self) -> None:
        page = _FakeMarketingOnlyPage()

        result = await offer._extract_offer_link(page)

        self.assertEqual(result, "")

    async def test_google_one_signin_visible_detects_anonymous_page(self) -> None:
        self.assertTrue(await offer._google_one_signin_visible(_FakeSigninPage(True)))
        self.assertFalse(await offer._google_one_signin_visible(_FakeSigninPage(False)))

    async def test_google_one_signin_visible_checks_past_hidden_first_match(self) -> None:
        self.assertTrue(await offer._google_one_signin_visible(_FakeMultiSigninPage()))


if __name__ == "__main__":
    unittest.main()
