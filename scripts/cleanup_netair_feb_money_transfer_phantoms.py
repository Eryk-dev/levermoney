#!/usr/bin/env python3
"""Clean up net-air Feb 2026 classifier mp_expenses that are:
  A) money_transfer / loan disbursement (now ERR-0027/0028 skip)
  B) phantom events not in extrato (Paramount+, collection attempts, etc.)

After deletion, re-runs extrato_ingester so it can ingest the proper
transferencia_pix_out / _in rows for the ref (since step (c) will no
longer be blocked by the classifier's row).
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.db.supabase import get_db
from app.services.extrato_ingester import ingest_extrato_from_csv


SELLER = "net-air"
MONTH = "2026-02"
CSV_PATH = PROJECT_ROOT / "testes" / "data" / "extratos" / "extrato fevereiro netair.csv"


async def main() -> int:
    db = get_db()

    # 1. Delete classifier money_transfer + loan disbursement rows for Feb
    ops_to_drop = ["money_transfer"]
    deleted_a = 0
    for op in ops_to_drop:
        mp = (
            db.table("mp_expenses")
            .select("payment_id")
            .eq("seller_slug", SELLER)
            .eq("operation_type", op)
            .gte("date_approved", "2026-02-01")
            .lt("date_approved", "2026-03-01")
            .range(0, 5000)
            .execute()
        )
        pids = [row["payment_id"] for row in (mp.data or []) if row.get("payment_id")]
        for pid in pids:
            try:
                ml_pid = int(str(pid).split(":")[0])
            except ValueError:
                continue
            res = (
                db.table("payment_events")
                .delete()
                .eq("seller_slug", SELLER)
                .eq("ml_payment_id", ml_pid)
                .eq("reference_id", str(ml_pid))
                .in_("event_type", ["expense_captured", "expense_classified"])
                .execute()
            )
            deleted_a += len(res.data or [])
    print(f"A) Deleted {deleted_a} money_transfer classifier rows")

    # 2. Delete phantom classifier rows (pids NOT in extrato CSV)
    csv_text = CSV_PATH.read_text(encoding="utf-8-sig")
    # Extract all refs from CSV (numeric only)
    extrato_refs: set[str] = set()
    for line in csv_text.splitlines():
        parts = line.split(";")
        if len(parts) >= 3 and parts[2].strip().isdigit():
            extrato_refs.add(parts[2].strip())
    print(f"Extrato has {len(extrato_refs)} distinct numeric refs")

    # Find classifier mp_expenses for Feb whose base pid is NOT in extrato
    classifier_ops = [
        "regular_payment", "recurring_payment",
    ]
    phantoms_to_drop: list[int] = []
    for op in classifier_ops:
        mp = (
            db.table("mp_expenses")
            .select("payment_id,expense_type,external_reference")
            .eq("seller_slug", SELLER)
            .eq("operation_type", op)
            .gte("date_approved", "2026-02-01")
            .lt("date_approved", "2026-03-01")
            .range(0, 5000)
            .execute()
        )
        for row in (mp.data or []):
            raw = str(row.get("payment_id") or "")
            base = raw.split(":")[0]
            if base and base not in extrato_refs:
                try:
                    phantoms_to_drop.append(int(base))
                except ValueError:
                    continue

    deleted_b = 0
    for pid in set(phantoms_to_drop):
        res = (
            db.table("payment_events")
            .delete()
            .eq("seller_slug", SELLER)
            .eq("ml_payment_id", pid)
            .eq("reference_id", str(pid))
            .in_("event_type", ["expense_captured", "expense_classified"])
            .execute()
        )
        deleted_b += len(res.data or [])
    print(f"B) Deleted {deleted_b} phantom classifier rows (pids not in extrato)")

    # 3. Re-run extrato_ingester to pick up now-uncovered refs
    res = await ingest_extrato_from_csv(SELLER, csv_text, MONTH)
    print("Re-ingest:")
    for k, v in res.items():
        if k in ("summary", "by_type"):
            continue
        print(f"  {k}: {v}")
    print(f"  by_type: {res.get('by_type')}")

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
