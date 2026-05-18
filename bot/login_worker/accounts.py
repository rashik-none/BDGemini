"""Async job-status helpers for the login worker (thin wrappers over bot.accounts)."""

from __future__ import annotations

from typing import Any

# Re-use the main accounts layer — no separate I/O needed anymore.
from bot.accounts import refund_job as _refund_job_main
from bot.accounts import update_job_status as _update_job_status_main


async def _update_job_status(
    telegram_id: str,
    job_id: str,
    status: str,
    extra: dict[str, Any] | None = None,
) -> None:
    await _update_job_status_main(telegram_id, job_id, status, extra)


async def _refund_job(telegram_id: str, job_id: str) -> None:
    await _refund_job_main(telegram_id, job_id)
