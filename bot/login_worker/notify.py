"""Telegram notification helpers."""

from __future__ import annotations

import asyncio
import logging
import re
from collections import OrderedDict
from html import escape as html_escape
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


_MAX_TRACKED_JOBS = 500
_JOB_RE = re.compile(r"\bJob\s+([a-zA-Z0-9_-]+)")
_JOB_LINE_RE = re.compile(r"^(?P<lead>.*?)<b>Job\s+(?P<job_id>[a-zA-Z0-9_-]+)</b>\s*$")
_TAG_RE = re.compile(r"<[^>]+>")
_JOB_MSG_IDS: OrderedDict[str, int] = OrderedDict()
_JOB_TICKS: OrderedDict[str, int] = OrderedDict()
_SPINNER_FRAMES = ("◜", "◠", "◝", "◞", "◡", "◟")
_RETRY_FRAMES = ("↺", "↻", "↺", "↻")
_PULSE_FRAMES = ("✦", "✧", "✦", "✧")
_BAR_HEAD_FRAMES = ("█", "▓", "▒", "▓")


def register_job_message(job_id: str, message_id: int) -> None:
    """Remember the status message that should be edited for a job."""
    if not job_id or not message_id:
        return

    _JOB_MSG_IDS[job_id] = message_id
    _JOB_MSG_IDS.move_to_end(job_id)
    _JOB_TICKS.setdefault(job_id, 0)
    _JOB_TICKS.move_to_end(job_id)

    _trim_tracked_jobs()


def _trim_tracked_jobs() -> None:
    while len(_JOB_MSG_IDS) > _MAX_TRACKED_JOBS:
        old_job_id, _ = _JOB_MSG_IDS.popitem(last=False)
        _JOB_TICKS.pop(old_job_id, None)
    while len(_JOB_TICKS) > _MAX_TRACKED_JOBS:
        old_job_id, _ = _JOB_TICKS.popitem(last=False)
        _JOB_MSG_IDS.pop(old_job_id, None)


def _message_id_for_text(text: str) -> int | None:
    match = _JOB_RE.search(text)
    if not match:
        return None

    job_id = match.group(1)
    msg_id = _JOB_MSG_IDS.get(job_id)
    if msg_id:
        _JOB_MSG_IDS.move_to_end(job_id)
    return msg_id


def _next_job_tick(job_id: str) -> int:
    tick = _JOB_TICKS.get(job_id, 0) + 1
    _JOB_TICKS[job_id] = tick
    _JOB_TICKS.move_to_end(job_id)
    _trim_tracked_jobs()
    return tick


def _strip_tags(value: str) -> str:
    return _TAG_RE.sub("", value or "").strip()


def _parse_job_notification(text: str) -> dict[str, Any] | None:
    lines = [line.rstrip() for line in text.strip().splitlines()]
    if not lines:
        return None

    match = _JOB_LINE_RE.match(lines[0].strip())
    if not match:
        return None

    content_lines = lines[1:]
    while content_lines and not content_lines[0].strip():
        content_lines.pop(0)
    while content_lines and not content_lines[-1].strip():
        content_lines.pop()

    title = content_lines.pop(0).strip() if content_lines else ""
    lead = _strip_tags(match.group("lead"))
    return {
        "job_id": match.group("job_id"),
        "lead": lead,
        "title": title,
        "body_lines": content_lines,
    }


def _classify_notification(title: str, body_lines: list[str], lead: str) -> dict[str, Any]:
    text = " ".join(part for part in (_strip_tags(lead), _strip_tags(title), *(_strip_tags(line) for line in body_lines)))
    normalized = text.lower()

    if any(key in normalized for key in ("offer claimed successfully", "job completed", "completed successfully")):
        return {"tone": "success", "tag": "COMPLETE", "progress": 100}
    if "plan already active" in normalized:
        return {"tone": "success", "tag": "COMPLETE", "progress": 100}
    if any(key in normalized for key in ("job failed", "offer claim error", "login failed", "wrong password")):
        return {"tone": "error", "tag": "FAILED", "progress": 100}
    if any(key in normalized for key in ("claim needs attention", "payment method required", "manual completion")):
        return {"tone": "action", "tag": "ACTION", "progress": 97}
    if "action needed on your phone" in normalized:
        return {"tone": "action", "tag": "APPROVE", "progress": 70}
    if any(key in normalized for key in ("retry scheduled", "trying next attempt", "skipping to next attempt")):
        return {"tone": "retry", "tag": "RETRY", "progress": 18}
    if any(key in normalized for key in ("proxy unreachable", "navigation timeout", "health check failed")):
        return {"tone": "warning", "tag": "NETWORK", "progress": 12}
    if any(key in normalized for key in ("claiming offer", "offer found", "no eligible offer found", "offer not eligible")):
        return {"tone": "active", "tag": "OFFER", "progress": 92}
    if "login verified" in normalized:
        return {"tone": "active", "tag": "LOGIN", "progress": 84}
    if any(key in normalized for key in ("verification submitted", "waiting for verification", "2fa", "totp")):
        return {"tone": "active", "tag": "VERIFY", "progress": 64}
    if any(key in normalized for key in ("submitting credentials", "password")):
        return {"tone": "active", "tag": "SECURE", "progress": 42}
    if any(key in normalized for key in ("checking gmail account", "email step", "email input")):
        return {"tone": "active", "tag": "ACCOUNT", "progress": 26}
    if any(key in normalized for key in ("navigating to google", "secure session", "connecting", "google login")):
        return {"tone": "active", "tag": "CONNECT", "progress": 12}
    if any(key in normalized for key in ("failed", "error", "unsafe browser", "invalid totp", "missing totp")):
        return {"tone": "error", "tag": "FAILED", "progress": 100}
    return {"tone": "active", "tag": "LIVE", "progress": 8}


def _header_frame(tone: str, tick: int) -> str:
    if tone == "success":
        return "✅"
    if tone == "error":
        return "❌"
    if tone == "warning":
        return "⚠️"
    if tone == "action":
        return _PULSE_FRAMES[tick % len(_PULSE_FRAMES)]
    if tone == "retry":
        return _RETRY_FRAMES[tick % len(_RETRY_FRAMES)]
    return _SPINNER_FRAMES[tick % len(_SPINNER_FRAMES)]


def _progress_bar(progress: int, tick: int, tone: str, width: int = 12) -> str:
    progress = max(0, min(100, int(progress)))
    filled = max(1 if progress else 0, round((progress / 100) * width))
    empty = max(0, width - filled)
    if 0 < filled < width and tone in {"active", "retry", "action"}:
        head = _BAR_HEAD_FRAMES[tick % len(_BAR_HEAD_FRAMES)]
        return ("█" * max(0, filled - 1)) + head + ("░" * empty)
    return ("█" * filled) + ("░" * empty)


def _format_title(title: str, lead: str) -> str:
    title = title.strip()
    if not title:
        title_html = "<b>Status update</b>"
    elif title.startswith("<b>") and title.endswith("</b>"):
        title_html = title
    elif "<" in title and ">" in title:
        title_html = title
    else:
        title_html = f"<b>{html_escape(title)}</b>"
    return f"{lead} {title_html}" if lead else title_html


def _format_body_lines(body_lines: list[str]) -> list[str]:
    formatted: list[str] = []
    previous_blank = False
    for raw_line in body_lines:
        line = raw_line.strip()
        if not line:
            if formatted and not previous_blank:
                formatted.append("")
            previous_blank = True
            continue
        previous_blank = False
        prefix = ""
        if not line.startswith(("•", "↩️", "▶️", "⏩", "💡", "📌", "📎")):
            prefix = "• "
        formatted.append(f"{prefix}{line}")
    return formatted


def _format_job_notification(text: str) -> str:
    parsed = _parse_job_notification(text)
    if not parsed:
        return text

    tick = _next_job_tick(parsed["job_id"])
    meta = _classify_notification(parsed["title"], parsed["body_lines"], parsed["lead"])
    body_lines = _format_body_lines(parsed["body_lines"])

    lines = [
        f"{_header_frame(meta['tone'], tick)} <b>Job {parsed['job_id']}</b>  <code>{meta['tag']}</code>",
        f"<code>{_progress_bar(meta['progress'], tick, meta['tone'])}</code> <b>{meta['progress']}%</b>",
        "",
        _format_title(parsed["title"], parsed["lead"]),
    ]
    if body_lines:
        lines.append("")
        lines.extend(body_lines)
    return "\n".join(lines).strip()


async def _send_text(bot: Any, chat_id: int, text: str) -> None:
    await bot.send_message(chat_id=chat_id, text=text, parse_mode="HTML")


async def _edit_text(bot: Any, chat_id: int, message_id: int, text: str) -> bool:
    from telegram.error import BadRequest, RetryAfter, TimedOut

    try:
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=text,
            parse_mode="HTML",
        )
        return True
    except RetryAfter as exc:
        await asyncio.sleep(float(exc.retry_after))
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=text,
            parse_mode="HTML",
        )
        return True
    except TimedOut:
        logger.warning("Timed out editing message %s in chat %s", message_id, chat_id)
        return False
    except BadRequest as exc:
        message = str(exc).lower()
        if "message is not modified" in message:
            return True

        logger.warning("Failed to edit message %s in chat %s: %s", message_id, chat_id, exc)
        return False


async def _notify(bot: Any, chat_id: int, text: str) -> None:
    """Edit the tracked job message when possible; otherwise send a new one."""
    try:
        rendered_text = _format_job_notification(text)
        msg_id = _message_id_for_text(text)
        if msg_id and await _edit_text(bot, chat_id, msg_id, rendered_text):
            return

        await _send_text(bot, chat_id, rendered_text)
    except Exception as exc:
        logger.warning("Failed to notify chat %s: %s", chat_id, exc)


async def _notify_photo(bot: Any, chat_id: int, photo_path: str, caption: str) -> None:
    """Send a screenshot to the user."""
    path = Path(photo_path)
    if not path.is_file():
        logger.warning("Screenshot not found for chat %s: %s", chat_id, photo_path)
        return

    try:
        with path.open("rb") as f:
            await bot.send_photo(chat_id=chat_id, photo=f, caption=caption, parse_mode="HTML")
    except Exception as exc:
        logger.warning("Failed to send photo to %s: %s", chat_id, exc)
