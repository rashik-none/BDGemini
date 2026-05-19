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
from bot.utils import user_identity


RECENT_JOB_LIMIT = 10
PROGRESS_WIDTH = 12


def short_text(value: str, limit: int = 18) -> str:
    value = value.strip()
    if limit <= 3:
        return value[:limit]
    if len(value) <= limit:
        return value
    return value[: limit - 3] + "..."


def status_emoji(status: str) -> str:
    return {
        "PENDING": "⏳",
        "RUNNING": "🟢",
        "PROCESSING": "⚙️",
        "SUCCESS": "✅",
        "SUCCEEDED": "✅",
        "LOGIN_OK": "🔐",
        "COMPLETED": "🎉",
        "FAILED": "❌",
        "ERROR": "💥",
    }.get(status.upper(), "❔")


def status_label(status: str) -> str:
    return {
        "PENDING": "Queued",
        "RUNNING": "Running",
        "PROCESSING": "Processing",
        "LOGIN_OK": "Login OK",
        "SUCCESS": "Success",
        "SUCCEEDED": "Success",
        "COMPLETED": "Completed",
        "FAILED": "Failed",
        "ERROR": "Error",
    }.get(status.upper(), status.replace("_", " ").title())


def status_badge(status: str) -> str:
    return f"{status_emoji(status)} <b>{escape(status_label(status))}</b>"


def stage_emoji(stage: str) -> str:
    """Return a unique emoji for each job stage."""
    stage_lower = stage.lower()
    if "start" in stage_lower:
        return "🚀"
    if "account" in stage_lower or "check" in stage_lower:
        return "🔍"
    if "credential" in stage_lower or "submit" in stage_lower:
        return "🔑"
    if "verif" in stage_lower or "wait" in stage_lower:
        return "📡"
    if "claim" in stage_lower or "offer" in stage_lower:
        return "🎁"
    if "final" in stage_lower:
        return "🏁"
    if "complet" in stage_lower:
        return "✅"
    if "fail" in stage_lower:
        return "❌"
    return "▪️"


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
    if status == "LOGIN_OK":
        return "Login confirmed"
    if status in {"SUCCESS", "SUCCEEDED", "COMPLETED"}:
        return "Completed"
    if status in {"FAILED", "ERROR"}:
        return "Failed"
    if progress < 10:
        return "Starting worker"
    if progress < 25:
        return "Opening login page"
    if progress < 40:
        return "Entering email"
    if progress < 60:
        return "Submitting credentials"
    if progress < 75:
        return "Handling 2FA/challenge"
    if progress < 90:
        return "Login confirmed"
    if progress < 95:
        return "Scanning for offers"
    if progress < 100:
        return "Claiming offer"
    return "Finalizing"


def progress_flow(progress: int, status: str) -> str:
    normalized = status.upper()
    completed = normalized in {"SUCCESS", "SUCCEEDED", "COMPLETED"}
    failed = normalized in {"FAILED", "ERROR"}
    steps = [
        (0, "Browser"),
        (25, "Login"),
        (60, "Verify"),
        (90, "Offer"),
        (100, "Done"),
    ]

    parts = []
    for idx, (start, label) in enumerate(steps):
        next_start = steps[idx + 1][0] if idx + 1 < len(steps) else 101
        if completed or progress >= next_start:
            marker = "●"
        elif not failed and progress >= start:
            marker = "◉"
        else:
            marker = "○"
        parts.append(f"{marker} {label}")
    return " › ".join(parts)


def main_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✨ Create Verify", callback_data="create_verify"),
                InlineKeyboardButton("📋 Recent Jobs", callback_data="recent_jobs"),
            ],
            [
                InlineKeyboardButton("💳 Balance", callback_data="balance"),
                InlineKeyboardButton("💸 Top Up", callback_data="topup"),
            ],
            [
                InlineKeyboardButton("👤 Profile", callback_data="profile"),
                InlineKeyboardButton("🎁 Referral", callback_data="ref"),
            ],
            [
                InlineKeyboardButton("📘 Guide", callback_data="guide"),
                InlineKeyboardButton("💰 Pricing", callback_data="pricing"),
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
        rows.append(
            [
                InlineKeyboardButton(
                    "📨 Share Invite",
                    url=f"https://t.me/share/url?url={quote(invite_link, safe='')}",
                )
            ]
        )
    rows.append([InlineKeyboardButton("🏠 Main Menu", callback_data="back_to_menu")])
    return InlineKeyboardMarkup(rows)


def recent_jobs_keyboard(account: dict) -> InlineKeyboardMarkup:
    rows = []
    for job in recent_jobs(account, RECENT_JOB_LIMIT):
        status = str(job.get("status", "PENDING")).upper()
        progress = max(0, min(100, safe_int(job.get("progress"))))
        email = short_text(str(job.get("gmail", "unknown")), 18)
        label = f"{status_emoji(status)} {progress:>3}% • {email}"
        job_id = job.get("id")
        if job_id:
            rows.append([InlineKeyboardButton(label, callback_data=f"job_{job_id}")])

    rows.append([InlineKeyboardButton("🏠 Main Menu", callback_data="back_to_menu")])
    return InlineKeyboardMarkup(rows)


def job_detail_keyboard(job_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("🔄 Refresh", callback_data=f"job_{job_id}"),
                InlineKeyboardButton("📋 All Jobs", callback_data="recent_jobs"),
            ],
            [InlineKeyboardButton("🏠 Main Menu", callback_data="back_to_menu")],
        ]
    )


def method_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🔐 2FA Secret", callback_data="verify_method_2fa")],
            [InlineKeyboardButton("✅ Verify Sign-In", callback_data="verify_method_signin")],
            [InlineKeyboardButton("✖ Cancel", callback_data="back_to_menu")],
        ]
    )


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
            f"{short_text(str(job.get('gmail', 'unknown')), 18)}{refunded}"
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


def start_message(update) -> str:
    user = update.effective_user
    user_name = (
        user.first_name
        if user and user.first_name
        else (user.username if user and user.username else DEFAULT_NAME or "User")
    )

    return (
        f"✨ <b>{escape(BOT_TITLE)}</b> ✨\n"
        f"{'━' * 26}\n\n"
        f"👋 Hello, <b>{escape(user_name)}</b>!\n\n"
        "<b>What I can do:</b>\n"
        "• 🔐 Auto-login to Gmail accounts\n"
        "• 🎁 Claim Pixel/Gemini offers\n"
        "• 📊 Track every step in real-time\n\n"
        f"{'─' * 26}\n"
        f"💰 <b>Price:</b> {VERIFY_PRICE} credit per job\n"
        f"{'─' * 26}\n\n"
        "📖 Open <b>Guide</b> before your first job."
    )


def profile_message(update, account: dict) -> str:
    telegram_id, username = user_identity(update)
    status_text = str(account.get('status', 'active'))
    status_icon = "🟢" if status_text == "active" else "🔴"

    return (
        "👤 <b>Account Profile</b>\n"
        f"{'━' * 26}\n\n"
        f"🆔 <b>Telegram ID:</b> <code>{escape(telegram_id)}</code>\n"
        f"👤 <b>Username:</b> @{escape(username.lstrip('@'))}\n"
        f"{status_icon} <b>Status:</b> {escape(status_text)}\n\n"
        f"{'─' * 26}\n"
        "💳 <b>Balance Overview</b>\n\n"
        f"   💰 Available: <b>{balance_credit(account)} credit</b>\n"
        f"   📥 Deposit:   {safe_int(account.get('deposit_credit'))} credit\n"
        f"   🎁 Referral:  {referral_credit(account)} credit\n\n"
        f"{'─' * 26}\n"
        "📊 <b>Activity</b>\n\n"
        f"   📥 Total deposited: {safe_int(account.get('total_deposit'))} credit\n"
        f"   📤 Total spent:     {total_spent(account)} credit"
    )


def balance_message(account: dict) -> str:
    total = balance_credit(account)
    deposit = safe_int(account.get('deposit_credit'))
    referral = referral_credit(account)

    return (
        "💳 <b>Balance</b>\n"
        f"{'━' * 26}\n\n"
        f"💰 <b>Available:</b> <b>{total} credit</b>\n\n"
        f"   📥 Deposit:  {deposit} credit\n"
        f"   🎁 Referral: {referral} credit\n\n"
        f"{'─' * 26}\n"
        f"💲 Verify job price: <b>{VERIFY_PRICE} credit</b>"
    )


def topup_message() -> str:
    return (
        "<b>Top Up Balance</b>\n\n"
        "Enter the amount you want to add as credit.\n"
        "Example: <code>10</code>"
    )


def create_verify_message() -> str:
    return (
        "<b>Start Verify</b>\n\n"
        "Send the Gmail address for this job.\n"
        "Example: <code>name@gmail.com</code>\n\n"
        "Only <code>@gmail.com</code> addresses are supported."
    )


def recent_jobs_message(account: dict) -> str:
    jobs = recent_jobs(account, RECENT_JOB_LIMIT)
    if not jobs:
        return (
            "📋 <b>Recent Jobs</b>\n"
            f"{'━' * 26}\n\n"
            "No jobs found yet.\n\n"
            "Tap <b>Create Verify</b> to start your first job."
        )

    shown = jobs[:RECENT_JOB_LIMIT]
    # Sort: running jobs first, then by progress descending
    pinned = sorted(
        shown,
        key=lambda job: (
            str(job.get("status", "PENDING")).upper() not in {"RUNNING", "PROCESSING", "PENDING"},
            -safe_int(job.get("progress")),
        ),
    )
    running = sum(
        1 for job in shown if str(job.get("status", "PENDING")).upper() in {"RUNNING", "PROCESSING", "PENDING"}
    )
    success = sum(
        1 for job in shown if str(job.get("status", "PENDING")).upper() in {"SUCCESS", "SUCCEEDED", "COMPLETED"}
    )
    failed = sum(1 for job in shown if str(job.get("status", "PENDING")).upper() in {"FAILED", "ERROR"})

    lines = [
        "📋 <b>Recent Jobs</b>",
        f"{'━' * 26}",
        "",
        f"🟢 Live <b>{running}</b>  •  ✅ Done <b>{success}</b>  •  ❌ Failed <b>{failed}</b>",
        "<i>Most active jobs stay at the top.</i>",
        "",
    ]

    for job in pinned:
        status = str(job.get("status", "PENDING")).upper()
        progress = max(0, min(100, safe_int(job.get("progress"))))
        stage = progress_stage(progress, status)
        note = str(job.get("progress_note", "")).strip()
        gmail = str(job.get("gmail", "unknown"))

        lines.append(f"● <code>{escape(gmail)}</code>")
        lines.append(f"   {status_badge(status)}  •  <b>{progress}%</b>")
        lines.append(f"   <code>{progress_bar(progress)}</code>  {stage_emoji(stage)} {escape(stage)}")
        if note:
            lines.append(f"   💬 {escape(short_text(note, 52))}")
        lines.append("")

    lines.append("Tap a job below to open the full status view.")
    return "\n".join(lines).strip()


def verify_builder_message(
    gmail: str = "",
    password: str = "",
    method: str = "",
    prompt: str = "Choose the next step.",
) -> str:
    if method.startswith("verify_method_"):
        method = "2FA Secret" if method.endswith("2fa") else "Verify Sign-In"

    completed_steps = sum(bool(value) for value in (gmail, password, method))
    step = min(4, completed_steps + 1)
    gmail_value = f"<code>{escape(gmail)}</code>" if gmail else "<i>Not set</i>"
    password_value = f"<code>{escape(password)}</code>" if password else "<i>Not set</i>"
    method_value = escape(method) if method else "<i>Not selected</i>"
    return (
        "<b>Create Verify Job</b>\n\n"
        f"Step {step}/4\n"
        f"Gmail: {gmail_value}\n"
        f"Password: {password_value}\n"
        f"Method: {method_value}\n\n"
        f"{prompt}"
    )


def verify_builder_keyboard(
    gmail: str = "",
    password: str = "",
    method: str = "",
    allow_review: bool = True,
) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton("Set Gmail", callback_data="verify_edit_gmail"),
            InlineKeyboardButton("Set Password", callback_data="verify_edit_password"),
        ],
        [InlineKeyboardButton("Choose Method", callback_data="verify_choose_method")],
    ]
    if allow_review:
        review_target = "verify_review" if gmail and password and method else "verify_builder"
        rows.append([InlineKeyboardButton("Review", callback_data=review_target)])
    rows.append([InlineKeyboardButton("Cancel", callback_data="back_to_menu")])
    return InlineKeyboardMarkup(rows)


def verify_review_message(gmail: str, password: str, method: str, price: int) -> str:
    return (
        "<b>Review Verify Job</b>\n\n"
        "Step 4/4\n"
        f"Gmail: <code>{escape(gmail)}</code>\n"
        f"Password: <code>{escape(password)}</code>\n"
        f"Method: <b>{escape(method)}</b>\n"
        f"Price: <b>{price} credit</b>\n\n"
        "For Verify Sign-In, keep the signed-in device online.\n"
        "For 2FA Secret, make sure the base32 secret is correct."
    )


def verify_review_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("Start Job", callback_data="verify_confirm_start")],
            [
                InlineKeyboardButton("Edit Gmail", callback_data="verify_edit_gmail"),
                InlineKeyboardButton("Edit Password", callback_data="verify_edit_password"),
            ],
            [InlineKeyboardButton("Edit Method", callback_data="verify_choose_method")],
            [InlineKeyboardButton("Cancel", callback_data="back_to_menu")],
        ]
    )


def verify_method_help_message() -> str:
    return (
        "<b>Choose Verification Method</b>\n\n"
        "<b>2FA Secret</b>\n"
        "Use this when you have the authenticator base32 secret.\n\n"
        "<b>Verify Sign-In</b>\n"
        "Use this when a Google sign-in prompt will appear on a logged-in device."
    )


def job_detail_message(job: dict) -> str:
    status = str(job.get("status", "PENDING")).upper()
    progress = max(0, min(100, safe_int(job.get("progress"))))
    completed = status in {"SUCCESS", "SUCCEEDED", "COMPLETED"} or bool(job.get("redeem_link"))
    failed = status in {"FAILED", "ERROR"}
    stage = progress_stage(progress, status)
    method = str(job.get("method", "N/A"))

    if failed:
        headline = "❌ <b>Job Failed</b>"
    elif completed:
        headline = "🎉 <b>Job Completed</b>"
    else:
        headline = "🟢 <b>Job Running</b>"

    lines = [
        headline,
        f"{'━' * 28}",
        "",
        f"{status_badge(status)}  •  {stage_emoji(stage)} <b>{escape(stage)}</b>",
        f"📊 <b>{progress}% complete</b>",
        f"<code>{progress_bar(progress)}</code>",
        f"🧭 <b>Flow:</b> {escape(progress_flow(progress, status))}",
        "",
    ]

    lines.append("📝 <b>Job Details</b>")
    lines.append(f"{'─' * 28}")
    lines.append("")
    lines.append(f"🆔 <b>ID:</b> <code>{escape(str(job.get('id', '')))}</code>")
    lines.append(f"📧 <b>Account:</b> <code>{escape(str(job.get('gmail', '')))}</code>")
    lines.append(f"🔐 <b>Method:</b> {escape(method)}")
    lines.append(f"💳 <b>Charged:</b> {safe_int(job.get('charged'))} credit ({escape(str(job.get('credit_source', 'N/A')))})")
    lines.append(f"📌 <b>Raw status:</b> <code>{escape(status)}</code>")

    offer_result = job.get("offer_result", "")
    if offer_result:
        lines.append("")
        lines.append("🎁 <b>Offer Result</b>")
        lines.append(f"{'─' * 28}")
        lines.append("")
        offer_icon = {
            "CLAIMED": "🎉", "ALREADY_ACTIVE": "ℹ️",
            "NOT_ELIGIBLE": "⚠️", "NOT_FOUND": "🔍",
            "PAYMENT_REQUIRED": "💳", "MANUAL_REQUIRED": "✋",
            "CLAIM_FAILED": "❌", "CLAIMABLE": "✨",
        }.get(str(offer_result).upper(), "❔")
        lines.append(f"   {offer_icon} Result: <b>{escape(str(offer_result))}</b>")
        offer_reason = job.get("offer_reason", "")
        if offer_reason:
            lines.append(f"💬 <b>Reason:</b> {escape(str(offer_reason))}")

    note = job.get("progress_note", "")
    if note:
        lines.append("")
        lines.append("💬 <b>Latest Update</b>")
        lines.append(f"{'─' * 28}")
        lines.append(escape(str(note)))

    redeem = job.get("redeem_link", "")
    if redeem:
        lines.append("")
        lines.append("🔗 <b>Redeem Link</b>")
        lines.append(f"{'─' * 28}")
        lines.append(escape(str(redeem)))

    error = job.get("error", "")
    if error:
        lines.append("")
        lines.append("⚠️ <b>Error Details</b>")
        lines.append(f"{'─' * 28}")
        lines.append(f"<code>{escape(str(error))}</code>")
        lines.append("")
        lines.append("💡 Fix the issue and retry with a new job.")

    refunded = job.get("refunded")
    if refunded:
        lines.append("")
        lines.append(f"↩️ <b>Refunded:</b> {safe_int(refunded)} credit")

    if not completed and not failed:
        lines.append("")
        lines.append(f"{'━' * 28}")
        lines.append("🔄 Tap <b>Refresh</b> for the latest progress.")

    return "\n".join(lines)


def referral_invite_link(update) -> str:
    telegram_id, _ = user_identity(update)
    return f"https://t.me/{BOT_USERNAME}?start=ref_{telegram_id}"


def referral_message(update, account: dict) -> str:
    invite_link = referral_invite_link(update)
    share_text = f"Join {BOT_TITLE} with my referral link: {invite_link}"

    return (
        f"<b>{escape(BOT_TITLE)} Referral</b>\n\n"
        "<b>Your invite link</b>\n"
        f"<code>{escape(invite_link)}</code>\n\n"
        f"Valid invited users: <b>{safe_int(account.get('valid_invited_users'))}</b>\n"
        f"Pending referrals: <b>{safe_int(account.get('pending_referrals'))}</b>\n"
        f"Earned referral credit: <b>{referral_earned_credit(account)}</b>\n"
        f"Available referral credit: <b>{referral_credit(account)}</b>\n"
        f"Remaining for next 1 credit: <b>{remaining_for_reward(account)}</b>\n\n"
        "<b>Share text</b>\n"
        f"<code>{escape(share_text)}</code>"
    )


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
            f"{escape(str(job.get('gmail', 'unknown')))} "
            f"<code>{escape(status)}</code>"
        )
    return "\n".join(lines)
