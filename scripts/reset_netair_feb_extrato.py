#!/usr/bin/env python3
"""Delete all extrato-sourced events for net-air Feb 2026 (scoped to Feb
only, so Jan data is preserved). Then re-ingest with the new ERR-0029 seed."""
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
    month = "2026-02"
    db = get_db()

    total = 0
    for etype in ("expense_captured", "expense_classified"):
        while True:
            res = (
                db.table("payment_events")
                .delete()
                .eq("seller_slug", seller)
                .eq("event_type", etype)
                .eq("source", "expense_lifecycle")
                .gte("event_date", "2026-02-01")
                .lte("event_date", "2026-02-28")
                .execute()
            )
            n = len(res.data or [])
            total += n
            if n < 1000:
                break
    print(f"Deleted {total} Feb extrato-sourced rows (Jan preserved)")

    csv_path = PROJECT_ROOT / "testes" / "data" / "extratos" / "extrato fevereiro netair.csv"
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
