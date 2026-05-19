"""Proxy pool parsing and selection."""

from __future__ import annotations

import asyncio
import logging
import os
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


def _load_proxy_list() -> list[dict[str, str]]:
    """Build the proxy pool from environment.

    Supported env vars (in priority order):
      PROXY_LIST  — newline-separated list of proxy URIs
                    e.g. "socks5://user:pass@host:port\\nsocks5://..."
      PROXY_URL   — single proxy URI (shorthand for one-proxy setups)

    Each URI is parsed into the dict format InvisiblePlaywright expects:
      {"server": "socks5://host:port", "username": "u", "password": "p"}
    """
    raw = os.getenv("PROXY_LIST", "").strip()
    if not raw:
        single = os.getenv("PROXY_URL", "").strip()
        if single:
            raw = single

    if not raw:
        return []

    proxies: list[dict[str, str]] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        proxies.append(_parse_proxy_uri(line))
    return proxies


def _parse_proxy_uri(uri: str) -> dict[str, str]:
    """Parse  scheme://[user:pass@]host:port  into a dict."""
    result: dict[str, str] = {}

    # Separate scheme
    if "://" in uri:
        scheme, rest = uri.split("://", 1)
    else:
        scheme, rest = "socks5", uri

    # Extract credentials if present
    if "@" in rest:
        creds, host_port = rest.rsplit("@", 1)
        if ":" in creds:
            result["username"], result["password"] = creds.split(":", 1)
        else:
            result["username"] = creds
    else:
        host_port = rest

    result["server"] = f"{scheme}://{host_port}"
    return result


def _pick_proxy(proxies: list[dict[str, str]], attempt: int) -> dict[str, str] | None:
    """Round-robin + jitter proxy selection."""
    if not proxies:
        return None
    return proxies[attempt % len(proxies)]


# ── Proxy health pre-check ───────────────────────────────────────────────────

async def _check_proxy_health(proxy: dict[str, str], timeout: float = 3.0) -> bool:
    """Quick TCP connect test to verify the proxy server is reachable.

    Returns True if the proxy host:port is connectable within *timeout*
    seconds. This avoids wasting the full 90s navigation timeout on a
    dead or expired proxy.

    Note: This only tests TCP reachability, not SOCKS/HTTP auth or Google
    connectivity. A passing test doesn't guarantee the proxy works, but
    a failing test guarantees it doesn't.
    """
    server = (proxy.get("server") or "").strip()
    if not server or server.lower() == "direct://":
        return True  # No proxy → always "healthy"

    try:
        parsed = urlparse(server)
        host = parsed.hostname
        port = parsed.port
        if not host or not port:
            logger.warning("Proxy health check: cannot parse host/port from %s", server)
            return False

        _, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port),
            timeout=timeout,
        )
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
        return True
    except (asyncio.TimeoutError, OSError, Exception) as exc:
        logger.warning("Proxy health check FAILED for %s: %s", server, exc)
        return False

