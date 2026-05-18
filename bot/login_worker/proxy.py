"""Proxy pool parsing and selection."""

from __future__ import annotations

import os


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
