"""Telegram notification helpers."""

from __future__ import annotations

import asyncio
import logging
import re
from collections import OrderedDict
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


_MAX_TRACKED_JOBS = 500
_JOB_RE = re.compile(r"\bJob\s+([a-zA-Z0-9_-]+)")
_JOB_MSG_IDS: OrderedDict[str, int] = OrderedDict()


def register_job_message(job_id: str, message_id: int) -> None:
    """Remember the status message that should be edited for a job."""
    if not job_id or not message_id:
        return

    _JOB_MSG_IDS[job_id] = message_id
    _JOB_MSG_IDS.move_to_end(job_id)

    while len(_JOB_MSG_IDS) > _MAX_TRACKED_JOBS:
        _JOB_MSG_IDS.popitem(last=False)


def _message_id_for_text(text: str) -> int | None:
    match = _JOB_RE.search(text)
    if not match:
        return None

    job_id = match.group(1)
    msg_id = _JOB_MSG_IDS.get(job_id)
    if msg_id:
        _JOB_MSG_IDS.move_to_end(job_id)
    return msg_id


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
        msg_id = _message_id_for_text(text)
        if msg_id and await _edit_text(bot, chat_id, msg_id, text):
            return

        await _send_text(bot, chat_id, text)
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


#  PAGE HELPERS
