#!/usr/bin/env python3
"""ERR-0023 cleanup: delete qr_pix_nao_sync + liberacao_nao_sync events for
net-air Jan 2026 (stale sign) and re-ingest."""
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

    # Fetch candidate rows via mp_expenses view (paginated)
    composite_ids: set[str] = set()
    start = 0
    page = 1000
    while True:
        mp = (
            db.table("mp_expenses")
            .select("payment_id")
            .eq("seller_slug", seller)
            .eq("expense_type", "qr_pix_nao_sync")
            .gte("date_approved", "2026-01-01")
            .lt("date_approved", "2026-02-01")
            .range(start, start + page - 1)
            .execute()
        )
        batch = mp.data or []
        composite_ids.update(row["payment_id"] for row in batch if row.get("payment_id"))
        if len(batch) < page:
            break
        start += page
    print(f"Found {len(composite_ids)} qr_pix_nao_sync mp_expenses in Jan 2026")

    deleted = 0
    for rid in composite_ids:
        res = (
            db.table("payment_events")
            .delete()
            .eq("seller_slug", seller)
            .eq("reference_id", rid)
            .in_("event_type", ["expense_captured", "expense_classified"])
            .execute()
        )
        deleted += len(res.data or [])

    print(f"Deleted {deleted} payment_events rows")

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
