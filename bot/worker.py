"""Thin re-export of the login worker public API."""

from __future__ import annotations

from bot.login_worker import start_login_job

__all__ = ["start_login_job"]
