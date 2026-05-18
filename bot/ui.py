"""Telegram UI helpers: keyboards, messages, formatting."""

from __future__ import annotations

from html import escape
from urllib.parse import quote
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from bot.accounts import (
    balance_credit,
    recent_jobs,
    referral_credit,
    referral_earned_credit,
    remaining_for_reward,
    total_spent,
)
from bot.config import BOT_TITLE, BOT_USERNAME, DEFAULT_NAME, VERIFY_PRICE


RECENT_JOB_LIMIT = 10
PROGRESS_WIDTH = 10


# ── Helpers ──────────────────────────────────────────────────────────

def short_text(value: str, limit: int = 18) -> str:
    value = value.strip()
    if limit <= 3:
        return value[:limit]
    if len(value) <= limit:
        return value
    return value[: limit - 3] + "..."


def mask_email(email: str) -> str:
    email = email.strip()
    if "@" not in email:
        return email

    local, domain = email.split("@", 1)
    if len(local) <= 2:
        masked_local = local[0] + "*" if local else "*"
    else:
        masked_local = f"{local[0]}{'*' * min(len(local) - 2, 5)}{local[-1]}"
    return f"{masked_local}@{domain}"


def status_emoji(status: str) -> str:
    return {
        "PENDING": "⏳",
        "RUNNING": "🔄",
        "PROCESSING": "⚙️",
        "SUCCESS": "✅",
        "SUCCEEDED": "✅",
        "LOGIN_OK": "🔓",
        "COMPLETED": "🎉",
        "FAILED": "❌",
        "ERROR": "💥",
    }.get(status.upper(), "❔")


def parse_positive_credit(value: str) -> int | None:
    cleaned = value.strip().replace(",", "")
    if cleaned.startswith("$"):
        cleaned = cleaned[1:].strip()
    if not cleaned.isdigit():
        return None

    try:
        amount = int(cleaned)
    except ValueError:
        return None
    return amount if amount > 0 else None


def safe_int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def progress_bar(progress: int) -> str:
    progress = max(0, min(100, progress))
    filled = round((progress / 100) * PROGRESS_WIDTH)
    empty = PROGRESS_WIDTH - filled
    return "█" * filled + "░" * empty


def progress_line(progress: int) -> str:
    return f"{progress_bar(progress)} {progress}%"


def progress_stage(progress: int, status: str) -> str:
    status = status.upper()
    if status in {"SUCCESS", "SUCCEEDED", "COMPLETED"}:
        return "Completed"
    if status in {"FAILED", "ERROR"}:
        return "Failed"
    if progress < 15:
        return "Starting secure session"
    if progress < 35:
        return "Checking Gmail account"
    if progress < 55:
        return "Submitting credentials"
    if progress < 75:
        return "Waiting for verification"
    if progress < 95:
        return "Claiming offer"
    return "Finalizing"


# ── Keyboards ────────────────────────────────────────────────────────

def main_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("👤 Profile", callback_data="profile"),
                InlineKeyboardButton("💳 Balance", callback_data="balance"),
            ],
            [
                InlineKeyboardButton("💰 Top up", callback_data="topup"),
                InlineKeyboardButton("✨ Create verify", callback_data="create_verify"),
            ],
            [
                InlineKeyboardButton("📋 Recent jobs", callback_data="recent_jobs"),
                InlineKeyboardButton("🏷️ Pricing", callback_data="pricing"),
            ],
            [
                InlineKeyboardButton("🇧🇩 Guide", callback_data="guide"),
                InlineKeyboardButton("🎁 Referral", callback_data="ref"),
            ],
            [InlineKeyboardButton("🌐 Language", callback_data="language")],
        ]
    )


def cancel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("✖ Cancel", callback_data="back_to_menu")]]
    )


def ref_keyboard(invite_link: str | None = None) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton("🔄 Refresh", callback_data="ref")]]
    if invite_link:
        rows.append([
            InlineKeyboardButton(
                "📤 Share invite",
                url=f"https://t.me/share/url?url={quote(invite_link, safe='')}",
            )
        ])
    rows.append([InlineKeyboardButton("🏠 Menu", callback_data="back_to_menu")])
    return InlineKeyboardMarkup(
        rows
    )


def recent_jobs_keyboard(account: dict) -> InlineKeyboardMarkup:
    rows = []
    for job in recent_jobs(account, RECENT_JOB_LIMIT):
        st = str(job.get("status", "PENDING")).upper()
        em = status_emoji(st)
        label = (
            f"{em} {short_text(mask_email(str(job.get('gmail', 'unknown'))), 22)} | "
            f"{st}"
        )
        job_id = job.get("id")
        if job_id:
            rows.append([InlineKeyboardButton(label, callback_data=f"job_{job_id}")])

    rows.append([InlineKeyboardButton("🏠 Menu", callback_data="back_to_menu")])
    return InlineKeyboardMarkup(rows)


def job_detail_keyboard(job_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Refresh", callback_data=f"job_{job_id}")],
        [InlineKeyboardButton("🏠 Menu", callback_data="back_to_menu")],
    ])


def method_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("2FA Secret", callback_data="verify_method_2fa")],
        [InlineKeyboardButton("Verify sign-in", callback_data="verify_method_signin")],
        [InlineKeyboardButton("✖ Cancel", callback_data="back_to_menu")],
    ])


def admin_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("📊 Stats", callback_data="admin_stats"),
                InlineKeyboardButton("👥 Users", callback_data="admin_users"),
            ],
            [
                InlineKeyboardButton("🔎 Lookup user", callback_data="admin_lookup"),
                InlineKeyboardButton("📣 Broadcast", callback_data="admin_broadcast"),
            ],
            [InlineKeyboardButton("📋 Recent jobs", callback_data="admin_recent_jobs")],
            [InlineKeyboardButton("🏠 User menu", callback_data="back_to_menu")],
        ]
    )


def admin_user_keyboard(telegram_id: str, status: str = "active") -> InlineKeyboardMarkup:
    status = status.lower()
    status_button = (
        InlineKeyboardButton("✅ Unban", callback_data=f"admin_unban_{telegram_id}")
        if status == "banned"
        else InlineKeyboardButton("🚫 Ban", callback_data=f"admin_ban_{telegram_id}")
    )
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("➕ Add credit", callback_data=f"admin_add_credit_{telegram_id}"),
                InlineKeyboardButton("➖ Remove credit", callback_data=f"admin_remove_credit_{telegram_id}"),
            ],
            [
                status_button,
                InlineKeyboardButton("📋 Jobs", callback_data=f"admin_user_jobs_{telegram_id}"),
            ],
            [InlineKeyboardButton("⬅ Admin", callback_data="admin_home")],
        ]
    )


def admin_jobs_keyboard(telegram_id: str, jobs: list[dict]) -> InlineKeyboardMarkup:
    rows = []
    for job in jobs[:10]:
        job_id = str(job.get("id", ""))
        if not job_id:
            continue
        refunded = " ↩" if job.get("refunded") else ""
        label = (
            f"{status_emoji(str(job.get('status', 'PENDING')))} "
            f"{short_text(mask_email(str(job.get('gmail', 'unknown'))), 18)}{refunded}"
        )
        rows.append([InlineKeyboardButton(label, callback_data=f"admin_refund_{telegram_id}_{job_id}")])
    rows.append([InlineKeyboardButton("⬅ User", callback_data=f"admin_user_{telegram_id}")])
    return InlineKeyboardMarkup(rows)


def admin_confirm_refund_keyboard(telegram_id: str, job_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("↩ Refund job", callback_data=f"admin_confirm_refund_{telegram_id}_{job_id}")],
            [InlineKeyboardButton("⬅ Jobs", callback_data=f"admin_user_jobs_{telegram_id}")],
        ]
    )


# ── Messages ─────────────────────────────────────────────────────────

def start_message(update) -> str:
    user = update.effective_user
    user_name = (
        user.first_name
        if user and user.first_name
        else (user.username if user and user.username else DEFAULT_NAME or "Muaj")
    )

    return (
        f"<b>🪄 {escape(BOT_TITLE)}</b>\n\n"
        f"Hi {escape(user_name)}, your account is ready.\n\n"
        "Use the menu below to top up, create verification jobs, track recent "
        "status, and collect completed redeem links.\n\n"
        "Before creating a job, check the Guide once so the Gmail account is "
        "prepared correctly.\n\n"
        f"<b>Price:</b> {VERIFY_PRICE} credit per job"
    )


def profile_message(update, account: dict) -> str:
    from bot.handlers import user_identity  # avoid circular
    telegram_id, username = user_identity(update)

    return (
        "<b>👤 Account profile</b>\n\n"
        f"🆔 Telegram ID: <code>{escape(telegram_id)}</code>\n"
        f"🪪 Username: @{escape(username.lstrip('@'))}\n"
        f"💳 Available balance: <b>{balance_credit(account)} credit</b>\n"
        f"💵 Deposit credit: {safe_int(account.get('deposit_credit'))}\n"
        f"🎁 Referral credit: {referral_credit(account)}\n"
        f"📥 Total deposit: {safe_int(account.get('total_deposit'))} credit\n"
        f"📤 Total spent: {total_spent(account)} credit\n"
        f"📌 Status: {escape(str(account.get('status', 'active')))}"
    )


def balance_message(account: dict) -> str:
    return (
        "<b>💳 Balance</b>\n\n"
        f"Available: <b>{balance_credit(account)} credit</b>\n"
        f"Deposit: {safe_int(account.get('deposit_credit'))} credit\n"
        f"Referral: {referral_credit(account)} credit\n\n"
        f"Verify job price: {VERIFY_PRICE} credit"
    )


def topup_message() -> str:
    return (
        "<b>💰 Top up</b>\n\n"
        "Enter the amount you want to add.\n"
        "Example: <code>10</code>"
    )


def create_verify_message() -> str:
    return (
        "<b>✨ Create verify</b>\n\n"
        "Send the Gmail address you want to verify.\n"
        "Only <code>@gmail.com</code> addresses are supported."
    )


def recent_jobs_message(account: dict) -> str:
    jobs = recent_jobs(account, RECENT_JOB_LIMIT)
    if jobs:
        return f"<b>📋 Recent jobs</b>\n\nShowing latest {len(jobs)} jobs."
    return "<b>📋 Recent jobs</b>\n\nNo jobs found yet."


def job_detail_message(job: dict) -> str:
    st = str(job.get("status", "PENDING")).upper()
    em = status_emoji(st)
    progress = max(0, min(100, safe_int(job.get("progress"))))
    completed = st in {"SUCCESS", "SUCCEEDED", "COMPLETED"} or bool(job.get("redeem_link"))
    failed = st in {"FAILED", "ERROR"}
    stage = progress_stage(progress, st)
    raw_email = str(job.get("gmail", ""))

    lines = [
        f"{em} <b>Job details</b>",
        "",
        f"<b>{escape(stage)}</b>",
        f"📈 <code>{progress_line(progress)}</code>",
        "",
        f"{em} Status: <b>{escape(st)}</b>",
        f"🆔 Job ID: <code>{escape(str(job.get('id', '')))}</code>",
        f"📧 Email: {escape(str(job.get('gmail', '')))}",
        f"💸 Charged: {safe_int(job.get('charged'))} credit",
        f"🏷️ Credit source: <b>{escape(str(job.get('credit_source', 'N/A')))}</b>",
    ]

    if raw_email:
        lines = [line.replace(raw_email, mask_email(raw_email)) for line in lines]

    note = job.get("progress_note", "")
    if note:
        lines.extend(["", f"📝 {escape(str(note))}"])

    redeem = job.get("redeem_link", "")
    if redeem:
        lines.extend(["", f"🔗 Redeem link: {escape(str(redeem))}"])

    error = job.get("error", "")
    if error:
        lines.extend(["", f"❌ Error: {escape(str(error))}"])

    refunded = job.get("refunded")
    if refunded:
        lines.append(f"↩️ Refunded: {safe_int(refunded)} credit")

    if not completed and not failed:
        lines.extend(["", "Use Refresh to get the latest progress."])

    return "\n".join(lines)


def referral_invite_link(update) -> str:
    from bot.handlers import user_identity
    telegram_id, _ = user_identity(update)
    return f"https://t.me/{BOT_USERNAME}?start=ref_{telegram_id}"


def referral_message(update, account: dict) -> str:
    invite_link = referral_invite_link(update)
    share_text = (
        f"Join {BOT_TITLE} with my referral link: "
        f"{invite_link}"
    )

    message = (
        f"<b>🎁 {escape(BOT_TITLE)} Referral</b>\n\n"
        f"🔗 <b>Your invite link</b>\n"
        f"<code>{escape(invite_link)}</code>\n\n"
        f"👥 Valid invited users: <b>{safe_int(account.get('valid_invited_users'))}</b>\n"
        f"⏳ Pending referrals: <b>{safe_int(account.get('pending_referrals'))}</b>\n"
        f"🏆 Earned referral credit: <b>{referral_earned_credit(account)}</b>\n"
        f"💳 Available referral credit: <b>{referral_credit(account)}</b>\n"
        f"🎯 Remaining for next 1 credit: <b>{remaining_for_reward(account)}</b>\n\n"
        "<b>Share text</b>\n"
        f"<code>{escape(share_text)}</code>"
    )
    return message


def simple_page(title: str, body: str) -> str:
    return f"<b>{escape(title)}</b>\n\n{escape(body)}"


def admin_dashboard_message(stats: dict[str, int]) -> str:
    return (
        "<b>🛠 Admin panel</b>\n\n"
        f"👥 Users: <b>{safe_int(stats.get('total_users'))}</b>\n"
        f"✅ Active: {safe_int(stats.get('active_users'))}\n"
        f"🚫 Banned: {safe_int(stats.get('banned_users'))}\n"
        f"💳 Total balance: {safe_int(stats.get('total_balance'))} credit\n"
        f"📥 Total deposit: {safe_int(stats.get('total_deposit'))} credit\n"
        f"📤 Total spent: {safe_int(stats.get('total_spent'))} credit\n"
        f"📋 Jobs: {safe_int(stats.get('total_jobs'))}\n"
        f"❌ Failed jobs: {safe_int(stats.get('failed_jobs'))}"
    )


def admin_lookup_prompt() -> str:
    return "<b>🔎 Lookup user</b>\n\nSend the Telegram user ID."


def admin_broadcast_prompt() -> str:
    return (
        "<b>📣 Broadcast</b>\n\n"
        "Send the HTML message to deliver to every account in accounts.json."
    )


def admin_users_message(user_ids: list[str]) -> str:
    if not user_ids:
        return "<b>👥 Users</b>\n\nNo accounts found."
    shown = "\n".join(f"• <code>{escape(user_id)}</code>" for user_id in user_ids[:20])
    more = "" if len(user_ids) <= 20 else f"\n\nShowing 20 of {len(user_ids)} users."
    return f"<b>👥 Users</b>\n\n{shown}{more}\n\nUse Lookup user to manage one user."


def admin_user_message(telegram_id: str, account: dict) -> str:
    jobs = recent_jobs(account, 5)
    return (
        "<b>👤 Admin user view</b>\n\n"
        f"🆔 ID: <code>{escape(telegram_id)}</code>\n"
        f"📌 Status: {escape(str(account.get('status', 'active')))}\n"
        f"💳 Balance: <b>{balance_credit(account)} credit</b>\n"
        f"💵 Deposit: {safe_int(account.get('deposit_credit'))} credit\n"
        f"🎁 Referral: {referral_credit(account)} credit\n"
        f"📥 Total deposit: {safe_int(account.get('total_deposit'))} credit\n"
        f"📤 Total spent: {total_spent(account)} credit\n"
        f"👥 Valid referrals: {safe_int(account.get('valid_invited_users'))}\n"
        f"📋 Recent jobs: {len(jobs)}"
    )


def admin_recent_jobs_message(items: list[tuple[str, dict]]) -> str:
    if not items:
        return "<b>📋 Admin recent jobs</b>\n\nNo jobs found."
    lines = ["<b>📋 Admin recent jobs</b>", ""]
    for telegram_id, job in items[:10]:
        status = str(job.get("status", "PENDING")).upper()
        lines.append(
            f"{status_emoji(status)} <code>{escape(telegram_id)}</code> "
            f"{escape(mask_email(str(job.get('gmail', 'unknown'))))} "
            f"<code>{escape(status)}</code>"
        )
    return "\n".join(lines)
