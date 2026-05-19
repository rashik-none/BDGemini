"""Telegram command & callback handlers."""

from __future__ import annotations

import logging
import os
import re

from html import escape
from telegram import Update
from telegram.error import BadRequest, RetryAfter, TimedOut
from telegram.ext import ContextTypes

from bot.accounts import (
    add_deposit,
    adjust_deposit_credit,
    admin_stats,
    all_recent_jobs,
    balance_credit,
    charge_account,
    create_job,
    default_account,
    get_account,
    list_account_ids,
    recent_jobs,
    refund_job,
    register_referral,
    save_account,
    set_account_status,
    update_job_status,
)
from bot.config import ADMIN_USER_IDS, VERIFY_PRICE
from bot.utils import mask_email, user_identity
from bot.ui import (
    admin_broadcast_prompt,
    admin_confirm_refund_keyboard,
    admin_dashboard_message,
    admin_jobs_keyboard,
    admin_keyboard,
    admin_lookup_prompt,
    admin_recent_jobs_message,
    admin_user_keyboard,
    admin_user_message,
    admin_users_message,
    balance_message,
    cancel_keyboard,
    create_verify_message,
    job_detail_keyboard,
    job_detail_message,
    main_keyboard,
    method_keyboard,
    parse_positive_credit,
    profile_message,
    recent_jobs_keyboard,
    recent_jobs_message,
    ref_keyboard,
    referral_message,
    referral_invite_link,
    simple_page,
    start_message,
    topup_message,
)


logger = logging.getLogger(__name__)

GMAIL_RE = re.compile(r"^[A-Z0-9._%+-]+@gmail\.com$", re.IGNORECASE)
INPUT_STATE_KEYS = (
    "awaiting_topup",
    "awaiting_verify",
    "awaiting_verify_gmail",
    "awaiting_verify_password",
    "awaiting_totp_secret",
    "verify_gmail",
    "verify_password",
    "verify_method",
    "verify_totp_secret",
    "admin_selected_user",
    "admin_credit_action",
    "awaiting_admin_lookup",
    "awaiting_admin_credit",
    "awaiting_admin_broadcast",
)
VERIFY_METHODS = {
    "verify_method_2fa": "2FA Secret",
    "verify_method_signin": "Verify sign-in",
}
STATIC_PAGES = {
    "pricing": ("🏷️ Pricing", f"Verify price: {VERIFY_PRICE} credit/job"),
    "guide": (
        "🇧🇩 Guide",
        "Use a real Gmail address, keep inbox and recovery access ready, then "
        "create a verify job. For Verify sign-in, keep the signed-in device "
        "online so it can receive the prompt.",
    ),
    "language": ("🌐 Language", "Current language: English"),
}


# ── Helpers ──────────────────────────────────────────────────────────


async def edit_message(query, text: str, reply_markup) -> None:
    try:
        await query.edit_message_text(
            text,
            parse_mode="HTML",
            reply_markup=reply_markup,
        )
    except RetryAfter as exc:
        logger.info("Telegram rate limited edit for %.1fs", float(exc.retry_after))
        return
    except TimedOut:
        logger.warning("Telegram timed out while editing callback message")
        return
    except BadRequest as exc:
        if "Message is not modified" in str(exc):
            return
        raise


def clear_input_state(context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context or context.user_data is None:
        return
    for key in INPUT_STATE_KEYS:
        context.user_data.pop(key, None)


async def delete_sensitive_message(update: Update) -> None:
    if not update.effective_message:
        return
    try:
        await update.effective_message.delete()
    except Exception:
        logger.debug("Could not delete sensitive user message", exc_info=True)


def valid_gmail(value: str) -> bool:
    return bool(GMAIL_RE.fullmatch(value.strip()))


def callback_chat_id(update: Update, query) -> int | None:
    if update.effective_chat:
        return update.effective_chat.id
    if query and query.message:
        return query.message.chat.id
    return None


def safe_referral_count(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def is_admin_id(telegram_id: str) -> bool:
    return telegram_id in ADMIN_USER_IDS


def is_banned(account: dict) -> bool:
    return str(account.get("status", "active")).lower() == "banned"


def account_disabled_message() -> str:
    return "<b>Account disabled</b>\n\nYour account is disabled. Contact support."


def admin_denied_message(telegram_id: str) -> str:
    configured = ", ".join(sorted(ADMIN_USER_IDS)) or "none"
    return (
        "<b>Access denied</b>\n\n"
        f"Your Telegram ID: <code>{escape(telegram_id)}</code>\n"
        f"Allowed admin IDs: <code>{escape(configured)}</code>"
    )


# ── /start ───────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_message:
        return

    telegram_id, _ = user_identity(update)
    referrer_id = None
    if context.args and context.args[0].startswith("ref_"):
        referrer_id = context.args[0].removeprefix("ref_").strip()

    await register_referral(telegram_id, referrer_id)
    await get_account(telegram_id)

    clear_input_state(context)
    await update.effective_message.reply_html(
        start_message(update),
        reply_markup=main_keyboard(),
    )


async def cmd_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_message:
        return
    if context.user_data is None:
        return

    telegram_id, _ = user_identity(update)
    if not is_admin_id(telegram_id):
        await update.effective_message.reply_html(admin_denied_message(telegram_id))
        return

    await get_account(telegram_id)
    clear_input_state(context)
    await update.effective_message.reply_html(
        admin_dashboard_message(await admin_stats()),
        reply_markup=admin_keyboard(),
    )


async def show_admin_user(query, telegram_id: str) -> None:
    account = await get_account(telegram_id)
    await edit_message(
        query,
        admin_user_message(telegram_id, account),
        admin_user_keyboard(telegram_id, str(account.get("status", "active"))),
    )


async def handle_admin_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.data:
        return
    if context.user_data is None:
        return

    telegram_id, _ = user_identity(update)
    if not is_admin_id(telegram_id):
        await edit_message(query, admin_denied_message(telegram_id), main_keyboard())
        return

    data = query.data

    if data in {"admin_home", "admin_stats"}:
        clear_input_state(context)
        await edit_message(query, admin_dashboard_message(await admin_stats()), admin_keyboard())
        return

    if data == "admin_users":
        clear_input_state(context)
        ids = await list_account_ids()
        await edit_message(query, admin_users_message(ids), admin_keyboard())
        return

    if data == "admin_lookup":
        clear_input_state(context)
        context.user_data["awaiting_admin_lookup"] = True
        await edit_message(query, admin_lookup_prompt(), admin_keyboard())
        return

    if data == "admin_broadcast":
        clear_input_state(context)
        context.user_data["awaiting_admin_broadcast"] = True
        await edit_message(query, admin_broadcast_prompt(), admin_keyboard())
        return

    if data == "admin_recent_jobs":
        clear_input_state(context)
        jobs = await all_recent_jobs(10)
        await edit_message(query, admin_recent_jobs_message(jobs), admin_keyboard())
        return

    if data.startswith("admin_user_jobs_"):
        target_id = data.removeprefix("admin_user_jobs_")
        account = await get_account(target_id)
        jobs = recent_jobs(account, 10)
        text = f"<b>📋 User jobs</b>\n\nUser: <code>{escape(target_id)}</code>"
        if not jobs:
            text += "\n\nNo jobs found."
        await edit_message(query, text, admin_jobs_keyboard(target_id, jobs))
        return

    if data.startswith("admin_user_"):
        target_id = data.removeprefix("admin_user_")
        clear_input_state(context)
        await show_admin_user(query, target_id)
        return

    if data.startswith("admin_add_credit_") or data.startswith("admin_remove_credit_"):
        adding = data.startswith("admin_add_credit_")
        target_id = data.removeprefix("admin_add_credit_" if adding else "admin_remove_credit_")
        target_account = await get_account(target_id)
        context.user_data["admin_selected_user"] = target_id
        context.user_data["admin_credit_action"] = "add" if adding else "remove"
        context.user_data["awaiting_admin_credit"] = True
        action = "add to" if adding else "remove from"
        await edit_message(
            query,
            f"<b>💳 Credit</b>\n\nSend amount to {action} <code>{escape(target_id)}</code>.",
            admin_user_keyboard(target_id, str(target_account.get("status", "active"))),
        )
        return

    if data.startswith("admin_ban_") or data.startswith("admin_unban_"):
        banning = data.startswith("admin_ban_")
        target_id = data.removeprefix("admin_ban_" if banning else "admin_unban_")
        await set_account_status(target_id, "banned" if banning else "active")
        await show_admin_user(query, target_id)
        return

    if data.startswith("admin_confirm_refund_"):
        payload = data.removeprefix("admin_confirm_refund_")
        target_id, _, job_id = payload.partition("_")
        ok = await refund_job(target_id, job_id)
        message = "Refunded successfully." if ok else "Job was already refunded or cannot be refunded."
        target_account = await get_account(target_id)
        await edit_message(
            query,
            f"<b>↩ Refund</b>\n\n{escape(message)}",
            admin_jobs_keyboard(target_id, recent_jobs(target_account, 10)),
        )
        return

    if data.startswith("admin_refund_"):
        payload = data.removeprefix("admin_refund_")
        target_id, _, job_id = payload.partition("_")
        target_account = await get_account(target_id)
        job = next((j for j in recent_jobs(target_account, 50) if str(j.get("id")) == job_id), None)
        if not job:
            await edit_message(query, "<b>↩ Refund</b>\n\nJob not found.", admin_keyboard())
            return
        text = (
            "<b>↩ Refund job?</b>\n\n"
            f"User: <code>{escape(target_id)}</code>\n"
            f"Job: <code>{escape(job_id)}</code>\n"
            f"Charged: {int(job.get('charged', 0))} credit\n"
            f"Refunded: {escape(str(job.get('refunded', False)))}"
        )
        await edit_message(query, text, admin_confirm_refund_keyboard(target_id, job_id))
        return

    await edit_message(query, "<b>Admin</b>\n\nUnknown action.", admin_keyboard())


# ── Callback queries ─────────────────────────────────────────────────

async def handle_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.data:
        return
    if context.user_data is None:
        return

    await query.answer()
    telegram_id, _ = user_identity(update)
    account = await get_account(telegram_id)

    if query.data.startswith("admin_"):
        await handle_admin_menu(update, context)
        return

    if is_banned(account) and not is_admin_id(telegram_id):
        clear_input_state(context)
        await edit_message(query, account_disabled_message(), main_keyboard())
        return

    if query.data in VERIFY_METHODS:
        gmail = context.user_data.get("verify_gmail")
        password = context.user_data.get("verify_password", "")
        method_key = query.data
        method = VERIFY_METHODS[method_key]
        worker_method = method
        persisted_method = method

        if not gmail:
            clear_input_state(context)
            await edit_message(query, create_verify_message(), cancel_keyboard())
            context.user_data["awaiting_verify_gmail"] = True
            return

        if not password:
            context.user_data["awaiting_verify_password"] = True
            await edit_message(
                query,
                "<b>✨ Create verify</b>\n\nEnter the Gmail password.",
                cancel_keyboard(),
            )
            return

        # ── 2FA method: need TOTP secret before creating job ──────────
        if method_key == "verify_method_2fa":
            totp_secret = context.user_data.get("verify_totp_secret", "")
            if not totp_secret:
                context.user_data["verify_method"] = method_key
                context.user_data["awaiting_totp_secret"] = True
                await edit_message(
                    query,
                    "<b>✨ Create verify</b>\n\n"
                    "Enter your <b>2FA / TOTP secret key</b> (base32).\n\n"
                    "This is the plain-text secret from your authenticator app "
                    "(not a 6-digit code). It looks like:\n"
                    "<code>JBSWY3DPEHPK3PXP</code>",
                    cancel_keyboard(),
                )
                return
            worker_method = f"2FA Secret:{totp_secret}"
            persisted_method = "2FA Secret"

        chat_id = callback_chat_id(update, query)
        if chat_id is None:
            clear_input_state(context)
            await edit_message(
                query,
                "<b>✨ Create verify</b>\n\nCould not resolve chat. Please try again.",
                main_keyboard(),
            )
            return

        charged, credit_source, charged_deposit, charged_referral = charge_account(account, VERIFY_PRICE)
        if not charged:
            clear_input_state(context)
            await edit_message(
                query,
                "<b>✨ Create verify</b>\n\n"
                f"Insufficient balance. You need {VERIFY_PRICE} credit.",
                main_keyboard(),
            )
            return

        job = create_job(
            account,
            str(gmail),
            str(password),
            persisted_method,
            VERIFY_PRICE,
            credit_source,
            charged_deposit,
            charged_referral,
        )
        await save_account(telegram_id, account)
        clear_input_state(context)

        await edit_message(
            query,
            "<b>✨ Create verify</b>\n\n"
            f"Job {escape(str(job['id']))} created for\n"
            f"{escape(mask_email(str(gmail)))}.\n"
            f"{VERIFY_PRICE} credit has been charged.\n"
            "Realtime tracking is now running.\n\n"
            f"Queue: {escape(credit_source)}\n"
            f"You are using the {escape(credit_source)} queue. "
            "You can create a new job every 5m.",
            job_detail_keyboard(str(job["id"])),
        )

        from bot.worker import start_login_job
        try:
            start_login_job(
                gmail=str(gmail),
                password=str(password),
                method=worker_method,
                job_id=str(job["id"]),
                telegram_id=telegram_id,
                bot=context.bot,
                chat_id=chat_id,
                message_id=query.message.message_id if query.message else None,
            )
        except Exception as exc:
            logger.exception("Failed to schedule login job %s", job.get("id"))
            await update_job_status(
                telegram_id,
                str(job["id"]),
                "FAILED",
                {"progress": 100, "progress_note": "Could not start worker", "error": str(exc)[:200]},
            )
            await refund_job(telegram_id, str(job["id"]))
            await edit_message(
                query,
                "<b>✨ Create verify</b>\n\n"
                "Could not start the worker. Your credit has been refunded.",
                main_keyboard(),
            )
        return

    if query.data.startswith("job_"):
        clear_input_state(context)
        job_id = query.data.removeprefix("job_")
        account = await get_account(telegram_id)
        job = next((item for item in recent_jobs(account, 50) if item.get("id") == job_id), None)
        if not job:
            await edit_message(query, "<b>📋 Job details</b>\n\nJob not found.", job_detail_keyboard(job_id))
            return
        await edit_message(query, job_detail_message(job), job_detail_keyboard(job_id))
        return

    clear_input_state(context)

    if query.data == "profile":
        await edit_message(query, profile_message(update, account), main_keyboard())
        return

    if query.data == "balance":
        await edit_message(query, balance_message(account), main_keyboard())
        return

    if query.data == "topup":
        context.user_data["awaiting_topup"] = True
        await edit_message(query, topup_message(), cancel_keyboard())
        return

    if query.data == "create_verify":
        context.user_data["awaiting_verify_gmail"] = True
        await edit_message(query, create_verify_message(), cancel_keyboard())
        return

    if query.data == "recent_jobs":
        await edit_message(query, recent_jobs_message(account), recent_jobs_keyboard(account))
        return

    if query.data == "ref":
        await edit_message(
            query,
            referral_message(update, account),
            ref_keyboard(referral_invite_link(update)),
        )
        return

    if query.data == "back_to_menu":
        await edit_message(query, start_message(update), main_keyboard())
        return

    title, body = STATIC_PAGES.get(query.data, ("Menu", "Unknown action."))
    await edit_message(query, simple_page(title, body), main_keyboard())


# ── Free-text input ──────────────────────────────────────────────────

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_message or not update.effective_message.text:
        return
    if context.user_data is None:
        return

    telegram_id, _ = user_identity(update)
    account = await get_account(telegram_id)
    text_value = update.effective_message.text

    # ── Admin flows ──────────────────────────────────────────────────
    if is_admin_id(telegram_id) and context.user_data.get("awaiting_admin_lookup"):
        target_id = text_value.strip()
        clear_input_state(context)
        if not target_id.isdigit():
            await update.effective_message.reply_html(
                "<b>🔎 Lookup user</b>\n\nSend a numeric Telegram ID.",
                reply_markup=admin_keyboard(),
            )
            return
        target_account = await get_account(target_id)
        await update.effective_message.reply_html(
            admin_user_message(target_id, target_account),
            reply_markup=admin_user_keyboard(target_id, str(target_account.get("status", "active"))),
        )
        return

    if is_admin_id(telegram_id) and context.user_data.get("awaiting_admin_credit"):
        target_id = str(context.user_data.get("admin_selected_user", ""))
        action = str(context.user_data.get("admin_credit_action", "add"))
        amount = parse_positive_credit(text_value)
        if amount is None:
            await update.effective_message.reply_html(
                "<b>💳 Credit</b>\n\nPlease enter a positive whole amount.",
                reply_markup=admin_keyboard(),
            )
            return
        delta = amount if action == "add" else -amount
        ok, new_credit = await adjust_deposit_credit(target_id, delta)
        clear_input_state(context)
        if not ok:
            await update.effective_message.reply_html(
                "<b>💳 Credit</b>\n\nUser not found.",
                reply_markup=admin_keyboard(),
            )
            return
        target_account = await get_account(target_id)
        await update.effective_message.reply_html(
            f"<b>💳 Credit updated</b>\n\n"
            f"User: <code>{escape(target_id)}</code>\n"
            f"Deposit credit: <b>{new_credit}</b>",
            reply_markup=admin_user_keyboard(target_id, str(target_account.get("status", "active"))),
        )
        return

    if is_admin_id(telegram_id) and context.user_data.get("awaiting_admin_broadcast"):
        message = text_value.strip()
        clear_input_state(context)
        if not message:
            await update.effective_message.reply_html(
                "<b>📣 Broadcast</b>\n\nMessage cannot be empty.",
                reply_markup=admin_keyboard(),
            )
            return
        safe_message = escape(message)
        sent = 0
        failed = 0
        for target_id in await list_account_ids():
            try:
                await context.bot.send_message(
                    chat_id=int(target_id),
                    text=safe_message,
                    parse_mode="HTML",
                )
                sent += 1
            except Exception as exc:
                failed += 1
                logger.warning("Broadcast failed for %s: %s", target_id, exc)
        await update.effective_message.reply_html(
            "<b>📣 Broadcast complete</b>\n\n"
            f"Sent: <b>{sent}</b>\n"
            f"Failed: <b>{failed}</b>",
            reply_markup=admin_keyboard(),
        )
        return

    if is_banned(account) and not is_admin_id(telegram_id):
        clear_input_state(context)
        await update.effective_message.reply_html(
            account_disabled_message(),
            reply_markup=main_keyboard(),
        )
        return

    # ── User flows ───────────────────────────────────────────────────
    if context.user_data.get("awaiting_verify_gmail"):
        gmail = text_value.strip().lower()
        if not valid_gmail(gmail):
            await update.effective_message.reply_html(
                "<b>✨ Create verify</b>\n\n"
                "Send a valid Gmail address, for example:\n"
                "<code>name@gmail.com</code>",
                reply_markup=cancel_keyboard(),
            )
            return
        context.user_data["verify_gmail"] = gmail
        context.user_data.pop("awaiting_verify_gmail", None)
        context.user_data["awaiting_verify_password"] = True
        await update.effective_message.reply_html(
            "<b>✨ Create verify</b>\n\nEnter the Gmail password.",
            reply_markup=cancel_keyboard(),
        )
        return

    if context.user_data.get("awaiting_verify_password"):
        password = text_value.strip()
        if not password:
            await update.effective_message.reply_html(
                "<b>✨ Create verify</b>\n\nPassword cannot be empty.",
                reply_markup=cancel_keyboard(),
            )
            return
        await delete_sensitive_message(update)
        context.user_data["verify_password"] = password
        context.user_data.pop("awaiting_verify_password", None)
        await update.effective_message.reply_html(
            "<b>✨ Create verify</b>\n\n"
            "Choose the sign-in verification method.\n\n"
            "If you choose Verify sign-in, the account must already be signed "
            "in on at least one device, and that device must have internet "
            "access to receive the Tap Yes/select-number prompt.",
            reply_markup=method_keyboard(),
        )
        return

    if context.user_data.get("awaiting_totp_secret"):
        secret = text_value.strip().replace(" ", "")
        if not secret or not re.fullmatch(r"[A-Z2-7=]+", secret.upper()):
            await update.effective_message.reply_html(
                "<b>✨ Create verify</b>\n\n"
                "❌ Invalid TOTP secret. It must be a base32 string, e.g.\n"
                "<code>JBSWY3DPEHPK3PXP</code>\n\n"
                "Send the correct secret or press Cancel.",
                reply_markup=cancel_keyboard(),
            )
            return
        await delete_sensitive_message(update)
        context.user_data["verify_totp_secret"] = secret.upper()
        context.user_data.pop("awaiting_totp_secret", None)
        context.user_data.pop("verify_method", None)
        await update.effective_message.reply_html(
            "<b>✨ Create verify</b>\n\n"
            "✅ TOTP secret saved. Confirm to create the job:",
            reply_markup=method_keyboard(),
        )
        return

    if not context.user_data.get("awaiting_topup"):
        return

    amount = parse_positive_credit(text_value)
    if amount is None:
        await update.effective_message.reply_html(
            "<b>💰 Top up</b>\n\nPlease enter a positive whole USD amount.",
            reply_markup=cancel_keyboard(),
        )
        return

    add_deposit(account, amount)
    await save_account(telegram_id, account)
    clear_input_state(context)

    await update.effective_message.reply_html(
        "<b>💰 Top up</b>\n\n"
        f"Deposit added: <b>{amount} credit</b>\n"
        f"Current balance: <b>{balance_credit(account)} credit</b>",
        reply_markup=main_keyboard(),
    )
