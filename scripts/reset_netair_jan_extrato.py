#!/usr/bin/env python3
"""Reset: delete all extrato-sourced events for net-air Jan 2026 and re-ingest
cleanly. Used after ERR-0023 partial cleanup left the state inconsistent."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.db.supabase import get_db
from app.services.extrato_ingester import ingest_extrato_from_csv


async def main() -> int:
    seller = "net-air"
    month = "2026-01"
    db = get_db()

    # Delete all expense_captured / expense_classified rows sourced from the
    # extrato for Jan 2026 (event_date within month).
    total_deleted = 0
    for etype in ("expense_captured", "expense_classified"):
        while True:
            res = (
                db.table("payment_events")
                .delete()
                .eq("seller_slug", seller)
                .eq("event_type", etype)
                .eq("source", "expense_lifecycle")
                .gte("event_date", "2026-01-01")
                .lte("event_date", "2026-01-31")
                .execute()
            )
            n = len(res.data or [])
            total_deleted += n
            if n < 1000:
                break
    print(f"Deleted {total_deleted} extrato-sourced payment_events rows")

    # Re-ingest
    csv_path = PROJECT_ROOT / "testes" / "data" / "extratos" / "extrato janeiro netair.csv"
    csv_text = csv_path.read_text(encoding="utf-8-sig")
    res = await ingest_extrato_from_csv(seller, csv_text, month)
    for k, v in res.items():
        if k in ("summary", "by_type"):
            continue
        print(f"  {k}: {v}")
    print(f"  by_type: {res.get('by_type')}")

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
