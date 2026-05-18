"""
migrate_json_to_mongo.py
========================
One-time migration: import all data from accounts.json into MongoDB.

Usage:
    python migrate_json_to_mongo.py [--dry-run]

Options:
    --dry-run   Preview what would be imported without writing to MongoDB.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

# ── Load env before importing bot modules ──────────────────────────────
from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent / "api.env")

from bot.database import ensure_indexes, get_db, ping  # noqa: E402


ACCOUNTS_FILE = Path(__file__).resolve().parent / "accounts.json"
DRY_RUN = "--dry-run" in sys.argv


async def migrate() -> None:
    print("=" * 60)
    print("  accounts.json -> MongoDB Migration")
    print("=" * 60)

    # ── Read source data first (works even without MongoDB) ────────
    if not ACCOUNTS_FILE.exists():
        print(f"[WARN] {ACCOUNTS_FILE} not found - nothing to migrate.")
        return

    with ACCOUNTS_FILE.open("r", encoding="utf-8") as f:
        data: dict = json.load(f)

    if not isinstance(data, dict):
        print("[ERROR] accounts.json is not a JSON object. Aborting.")
        sys.exit(1)

    total = len(data)
    print(f"[INFO] Found {total} user(s) in accounts.json.\n")

    if DRY_RUN:
        print("[DRY RUN] No data will be written.\n")
        for tid, account in data.items():
            jobs = len(account.get("jobs", []))
            print(f"  Would upsert user {tid} ({jobs} job(s))")
        print(f"\nTotal: {total} user(s) would be migrated.")
        return

    # ── Verify connectivity before writing ─────────────────────────
    if not await ping():
        print("[ERROR] Cannot reach MongoDB. Check MONGODB_URI in api.env.")
        sys.exit(1)
    print("[OK] MongoDB connected.\n")

    # ── Migrate ────────────────────────────────────────────────────
    col = get_db()["users"]
    await ensure_indexes()

    inserted = 0
    errors = 0

    for telegram_id, account in data.items():
        try:
            payload = {k: v for k, v in account.items() if k != "_id"}
            # $setOnInsert: only writes if the document does NOT already exist
            await col.update_one(
                {"_id": telegram_id},
                {"$setOnInsert": payload},
                upsert=True,
            )
            inserted += 1
            jobs_count = len(account.get("jobs", []))
            print(f"  [OK] {telegram_id} - {jobs_count} job(s)")
        except Exception as exc:
            errors += 1
            print(f"  [ERR] {telegram_id} - {exc}")

    print(f"\n{'=' * 60}")
    print(f"  Done: {inserted} migrated, {errors} errors")
    print(f"{'=' * 60}")

    if errors == 0:
        print("\nTip: You can now rename accounts.json to accounts.json.bak")


if __name__ == "__main__":
    asyncio.run(migrate())
