import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import bot.accounts as accounts
import bot.handlers as handlers
import bot.worker as worker


class AccountAuditTests(unittest.IsolatedAsyncioTestCase):
    async def test_mixed_charge_refunds_deposit_and_referral_once(self) -> None:
        original_file = accounts.ACCOUNTS_FILE
        original_mongo_available = accounts.mongo_available
        try:
            with tempfile.TemporaryDirectory() as tmp:
                accounts.ACCOUNTS_FILE = Path(tmp) / "accounts.json"
                accounts.mongo_available = lambda: False

                account = accounts.default_account()
                account["deposit_credit"] = 1
                account["valid_invited_users"] = 10

                charged, source, charged_deposit, charged_referral = accounts.charge_account(account, 2)
                self.assertTrue(charged)
                self.assertEqual(source, "DEPOSIT+REFERRAL")
                self.assertEqual((charged_deposit, charged_referral), (1, 1))

                job = accounts.create_job(
                    account,
                    "person@gmail.com",
                    "2FA Secret",
                    2,
                    source,
                    charged_deposit,
                    charged_referral,
                )
                await accounts.save_account("123", account)

                self.assertTrue(await accounts.refund_job("123", job["id"]))
                self.assertFalse(await accounts.refund_job("123", job["id"]))

                saved = await accounts.get_account("123")
                self.assertEqual(saved["deposit_credit"], 1)
                self.assertEqual(saved["deposit_spent"], 0)
                self.assertEqual(saved["referral_spent"], 0)
                self.assertEqual(saved["jobs"][0]["refunded"], 2)
        finally:
            accounts.ACCOUNTS_FILE = original_file
            accounts.mongo_available = original_mongo_available

    async def test_mongo_refund_uses_idempotent_filter(self) -> None:
        original_mongo_available = accounts.mongo_available
        original_find_user = accounts._find_user
        original_users_col = accounts.users_col

        class _Result:
            modified_count = 0

        class _Collection:
            def __init__(self) -> None:
                self.calls = []

            async def update_one(self, *args, **kwargs):
                self.calls.append((args, kwargs))
                return _Result()

        col = _Collection()
        try:
            accounts.mongo_available = lambda: True

            async def fake_find_user(_telegram_id: str) -> dict:
                return {
                    "jobs": [
                        {
                            "id": "job1",
                            "charged": 2,
                            "charged_deposit": 1,
                            "charged_referral": 1,
                        }
                    ]
                }

            accounts._find_user = fake_find_user
            accounts.users_col = lambda: col

            self.assertFalse(await accounts.refund_job("123", "job1"))
            update_filter = col.calls[0][0][0]
            update_body = col.calls[0][0][1]
            array_filters = col.calls[0][1]["array_filters"]

            self.assertEqual(
                update_filter["jobs"]["$elemMatch"]["refunded"],
                {"$exists": False},
            )
            self.assertEqual(array_filters[0]["job.refunded"], {"$exists": False})
            self.assertEqual(update_body["$inc"]["deposit_credit"], 1)
            self.assertEqual(update_body["$inc"]["deposit_spent"], -1)
            self.assertEqual(update_body["$inc"]["referral_spent"], -1)
        finally:
            accounts.mongo_available = original_mongo_available
            accounts._find_user = original_find_user
            accounts.users_col = original_users_col


class _FakeQuery:
    def __init__(self, data: str) -> None:
        self.data = data
        self.message = SimpleNamespace(chat=SimpleNamespace(id=555), message_id=99)
        self.edits = []

    async def answer(self) -> None:
        return None

    async def edit_message_text(self, text: str, parse_mode=None, reply_markup=None) -> None:
        self.edits.append((text, parse_mode, reply_markup))


class _FakeMessage:
    def __init__(self, text: str = "") -> None:
        self.text = text
        self.deleted = False
        self.replies = []

    async def delete(self) -> None:
        self.deleted = True

    async def reply_html(self, text: str, reply_markup=None) -> None:
        self.replies.append((text, reply_markup))


class _FakeUpdate:
    def __init__(self, query_data: str | None = None, text: str = "") -> None:
        self.effective_user = SimpleNamespace(id=123, username="tester", first_name="Tester")
        self.effective_chat = SimpleNamespace(id=555)
        self.effective_message = _FakeMessage(text)
        self.callback_query = _FakeQuery(query_data) if query_data else None


class _FakeContext:
    def __init__(self, user_data: dict) -> None:
        self.user_data = user_data
        self.bot = object()
        self.args = []


class HandlerSecretTests(unittest.IsolatedAsyncioTestCase):
    async def test_totp_secret_not_persisted_and_state_cleared_on_job_create(self) -> None:
        original_get_account = handlers.get_account
        original_save_account = handlers.save_account
        original_start_login_job = worker.start_login_job
        original_verify_price = handlers.VERIFY_PRICE
        account = accounts.default_account()
        account["deposit_credit"] = 1
        saved_accounts = []
        started = {}

        try:
            async def fake_get_account(_telegram_id: str) -> dict:
                return account

            async def fake_save_account(_telegram_id: str, saved: dict) -> None:
                saved_accounts.append(saved.copy())

            def fake_start_login_job(**kwargs):
                started.update(kwargs)
                return None

            handlers.get_account = fake_get_account
            handlers.save_account = fake_save_account
            worker.start_login_job = fake_start_login_job
            handlers.VERIFY_PRICE = 1

            context = _FakeContext(
                {
                    "verify_gmail": "person@gmail.com",
                    "verify_password": "pw",
                    "verify_totp_secret": "JBSWY3DPEHPK3PXP",
                }
            )
            await handlers.handle_menu(_FakeUpdate("verify_method_2fa"), context)

            self.assertEqual(account["jobs"][0]["method"], "2FA Secret")
            self.assertNotIn("JBSWY3DPEHPK3PXP", str(account["jobs"][0]))
            self.assertEqual(started["method"], "2FA Secret:JBSWY3DPEHPK3PXP")
            self.assertNotIn("verify_password", context.user_data)
            self.assertNotIn("verify_totp_secret", context.user_data)
            self.assertTrue(saved_accounts)
        finally:
            handlers.get_account = original_get_account
            handlers.save_account = original_save_account
            worker.start_login_job = original_start_login_job
            handlers.VERIFY_PRICE = original_verify_price

    async def test_cancel_clears_sensitive_state(self) -> None:
        original_get_account = handlers.get_account
        try:
            async def fake_get_account(_telegram_id: str) -> dict:
                return accounts.default_account()

            handlers.get_account = fake_get_account
            context = _FakeContext(
                {
                    "verify_password": "pw",
                    "verify_totp_secret": "JBSWY3DPEHPK3PXP",
                    "awaiting_totp_secret": True,
                }
            )
            await handlers.handle_menu(_FakeUpdate("back_to_menu"), context)
            self.assertNotIn("verify_password", context.user_data)
            self.assertNotIn("verify_totp_secret", context.user_data)
            self.assertNotIn("awaiting_totp_secret", context.user_data)
        finally:
            handlers.get_account = original_get_account

    async def test_sensitive_text_messages_are_deleted_when_saved(self) -> None:
        update = _FakeUpdate(text="pw")
        context = _FakeContext({"awaiting_verify_password": True})
        await handlers.handle_text(update, context)
        self.assertTrue(update.effective_message.deleted)
        self.assertEqual(context.user_data["verify_password"], "pw")


if __name__ == "__main__":
    unittest.main()
