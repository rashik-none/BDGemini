import unittest

import bot.login_worker.page as page


class PageHelperTests(unittest.TestCase):
    def test_mask_email_redacts_local_part(self) -> None:
        self.assertEqual(page._mask_email("person@example.com"), "p****n@example.com")
        self.assertEqual(page._mask_email("not-an-email"), "not-an-email")

    def test_safe_proxy_label_hides_credentials(self) -> None:
        proxy = {"server": "http://user:pass@proxy.example.com:8080"}
        self.assertEqual(page._safe_proxy_label(proxy), "http://proxy.example.com:8080")

    def test_safe_proxy_label_handles_bad_port(self) -> None:
        proxy = {"server": "http://proxy.example.com:bad"}
        self.assertEqual(page._safe_proxy_label(proxy), "http://proxy.example.com")

    def test_check_markers_is_case_insensitive(self) -> None:
        self.assertTrue(page._check_markers("Wrong Password", ["wrong password"]))

    def test_redact_sensitive_handles_non_string_text(self) -> None:
        self.assertEqual(page._redact_sensitive(123, "2"), "1[redacted]3")


if __name__ == "__main__":
    unittest.main()
