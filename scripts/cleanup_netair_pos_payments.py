#!/usr/bin/env python3
"""ERR-0022 cleanup: remove pos_payment events for net-air + re-ingest extrato.

The old classifier wrote `other/expense` rows for presencial POS sales. After
the fix (pos_payment → skip), these events must be deleted so the extrato
liberacao line can be ingested as `liberacao_nao_sync` for the same ref.
"""
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

    # Find all pos_payment-derived events via mp_expenses view (which maps
    # expense_captured + companion expense_classified per ref).
    mp = (
        db.table("mp_expenses")
        .select("payment_id")
        .eq("seller_slug", seller)
        .eq("operation_type", "pos_payment")
        .gte("date_approved", "2026-01-01")
        .lt("date_approved", "2026-02-01")
        .execute()
    )
    pids = list({row["payment_id"] for row in (mp.data or []) if row.get("payment_id")})
    print(f"Found {len(pids)} pos_payment mp_expenses in Jan 2026")

    deleted_total = 0
    for pid in pids:
        # Cast pid to int when possible (ml_payment_id is integer column)
        try:
            ml_pid = int(str(pid).split(":")[0])
        except ValueError:
            continue
        res = (
            db.table("payment_events")
            .delete()
            .eq("seller_slug", seller)
            .eq("ml_payment_id", ml_pid)
            .in_("event_type", ["expense_captured", "expense_classified"])
            .execute()
        )
        deleted_total += len(res.data or [])

    print(f"Deleted {deleted_total} payment_events rows (expense_captured/expense_classified)")

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
