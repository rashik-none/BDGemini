"""TOTP parsing and code generation helpers."""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import os
import struct
import time


def _is_totp_method(method: str) -> bool:
    return method.strip().lower().startswith(("2fa secret", "totp"))


def _extract_totp_secret(method: str) -> str:
    cleaned = method.strip()
    for separator in (":", "=", "|"):
        if separator in cleaned:
            label, secret = cleaned.split(separator, 1)
            if _is_totp_method(label):
                return secret.strip()

    lowered = cleaned.lower()
    for prefix in ("2fa secret", "totp"):
        if lowered.startswith(prefix) and len(cleaned) > len(prefix):
            return cleaned[len(prefix):].strip(" :|=")

    return (
        os.getenv("TOTP_SECRET", "").strip()
        or os.getenv("GOOGLE_TOTP_SECRET", "").strip()
    )


def _generate_totp(secret: str, timestamp: float | None = None) -> str:
    normalized = "".join(secret.upper().split())
    padding = "=" * ((8 - len(normalized) % 8) % 8)
    try:
        key = base64.b32decode(normalized + padding, casefold=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("invalid TOTP secret") from exc

    counter = int((timestamp if timestamp is not None else time.time()) // 30)
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return f"{code % 1_000_000:06d}"
