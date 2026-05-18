"""Compatibility shim for the modular login worker package."""

from __future__ import annotations

from bot.login_worker import register_job_message, start_login_job

__all__ = ["start_login_job", "register_job_message"]
