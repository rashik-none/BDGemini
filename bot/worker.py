"""Login worker manager — job lifecycle, concurrency tracking, and cleanup.

This module wraps the raw ``start_login_job`` from the login_worker package
with production-grade job management:

  • Prevents duplicate jobs for the same Gmail address.
  • Tracks in-flight asyncio Tasks and exposes helpers for admin inspection.
  • Enforces a per-user concurrency limit (default 1 job at a time).
  • Provides graceful cancellation and cleanup on shutdown.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from bot.login_worker import register_job_message
from bot.login_worker.runner import start_login_job as _raw_start_login_job

logger = logging.getLogger(__name__)

# ── In-flight job registry ───────────────────────────────────────────

# job_id → {"task": asyncio.Task, "gmail": str, "telegram_id": str, "started": float}
_active_jobs: dict[str, dict[str, Any]] = {}

# Per-user concurrency limit.  Set to 0 for unlimited.
MAX_CONCURRENT_PER_USER = 1


# ── Public API ───────────────────────────────────────────────────────

def start_login_job(
    gmail: str,
    password: str,
    method: str,
    job_id: str,
    telegram_id: str,
    bot: Any,
    chat_id: int,
    message_id: int | None = None,
) -> asyncio.Task:
    """Schedule a login job with concurrency and duplicate guards.

    Raises
    ------
    RuntimeError
        If the user already has ``MAX_CONCURRENT_PER_USER`` jobs in flight,
        or if a job for the same Gmail address is already running.
    """

    _gc_finished_jobs()

    gmail_lower = gmail.strip().lower()

    # ── Guard: duplicate Gmail ────────────────────────────────────────
    for jid, meta in _active_jobs.items():
        if meta["gmail"] == gmail_lower and not meta["task"].done():
            raise RuntimeError(
                f"A job for {gmail_lower} is already running (job {jid})."
            )

    # ── Guard: per-user concurrency ───────────────────────────────────
    if MAX_CONCURRENT_PER_USER > 0:
        user_running = sum(
            1
            for meta in _active_jobs.values()
            if meta["telegram_id"] == telegram_id and not meta["task"].done()
        )
        if user_running >= MAX_CONCURRENT_PER_USER:
            raise RuntimeError(
                f"You already have {user_running} job(s) running. "
                f"Maximum concurrent jobs per user is {MAX_CONCURRENT_PER_USER}."
            )

    # ── Dispatch to the real runner ───────────────────────────────────
    task = _raw_start_login_job(
        gmail=gmail,
        password=password,
        method=method,
        job_id=job_id,
        telegram_id=telegram_id,
        bot=bot,
        chat_id=chat_id,
        message_id=message_id,
    )

    _active_jobs[job_id] = {
        "task": task,
        "gmail": gmail_lower,
        "telegram_id": telegram_id,
        "started": time.time(),
    }

    # Auto-remove from registry when done (fire-and-forget callback).
    task.add_done_callback(lambda _t: _on_job_done(job_id))
    logger.info(
        "Job %s scheduled for %s (user %s) — %d active",
        job_id, gmail_lower, telegram_id, active_job_count(),
    )
    return task


def cancel_job(job_id: str) -> bool:
    """Cancel a running job by job_id.  Returns True if cancelled."""
    meta = _active_jobs.get(job_id)
    if not meta:
        return False
    task = meta["task"]
    if task.done():
        return False
    task.cancel()
    logger.info("Job %s cancelled", job_id)
    return True


def active_job_count() -> int:
    """Return the number of currently in-flight jobs."""
    _gc_finished_jobs()
    return sum(1 for meta in _active_jobs.values() if not meta["task"].done())


def active_jobs_for_user(telegram_id: str) -> list[str]:
    """Return job_ids for a user's in-flight jobs."""
    _gc_finished_jobs()
    return [
        jid
        for jid, meta in _active_jobs.items()
        if meta["telegram_id"] == telegram_id and not meta["task"].done()
    ]


def active_jobs_summary() -> list[dict[str, Any]]:
    """Return a list of summary dicts for all active jobs (admin inspection)."""
    _gc_finished_jobs()
    now = time.time()
    return [
        {
            "job_id": jid,
            "gmail": meta["gmail"],
            "telegram_id": meta["telegram_id"],
            "elapsed_s": round(now - meta["started"], 1),
            "done": meta["task"].done(),
        }
        for jid, meta in _active_jobs.items()
        if not meta["task"].done()
    ]


async def shutdown_all(timeout: float = 30.0) -> int:
    """Cancel every running job and wait up to *timeout* seconds.

    Called during bot shutdown to ensure clean browser cleanup.
    Returns the number of jobs that were cancelled.
    """
    _gc_finished_jobs()
    running = [
        (jid, meta["task"])
        for jid, meta in _active_jobs.items()
        if not meta["task"].done()
    ]
    if not running:
        return 0

    logger.info("Shutting down %d active job(s)…", len(running))
    for jid, task in running:
        task.cancel()

    tasks = [task for _, task in running]
    await asyncio.gather(*tasks, return_exceptions=True)
    _active_jobs.clear()
    logger.info("All jobs shut down.")
    return len(running)


# ── Internal helpers ─────────────────────────────────────────────────

def _on_job_done(job_id: str) -> None:
    """Callback fired when a job task completes."""
    meta = _active_jobs.get(job_id)
    if meta:
        elapsed = time.time() - meta["started"]
        task = meta["task"]
        if task.cancelled():
            logger.info("Job %s was cancelled after %.1fs", job_id, elapsed)
        elif task.exception():
            logger.warning(
                "Job %s crashed after %.1fs: %s",
                job_id, elapsed, task.exception(),
            )
        else:
            logger.info("Job %s completed in %.1fs", job_id, elapsed)


def _gc_finished_jobs() -> None:
    """Remove completed/cancelled jobs older than 5 minutes from the registry."""
    cutoff = time.time() - 300
    stale = [
        jid
        for jid, meta in _active_jobs.items()
        if meta["task"].done() and meta["started"] < cutoff
    ]
    for jid in stale:
        del _active_jobs[jid]


__all__ = [
    "start_login_job",
    "register_job_message",
    "cancel_job",
    "active_job_count",
    "active_jobs_for_user",
    "active_jobs_summary",
    "shutdown_all",
]
