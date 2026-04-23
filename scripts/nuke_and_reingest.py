#!/usr/bin/env python3
"""Delete all extrato-ingester-created events (suffix payment_id) and re-ingest.

Only removes payment_events with event_type in {expense_captured, expense_classified}
and reference_id containing ':' (composite suffix marker). Classifier-created events
(no suffix) and processor-created events (sale_approved, fee_charged, etc) are preserved.

Args: seller period csv_filename
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
    if len(sys.argv) != 4:
        print("Usage: nuke_and_reingest.py <seller> <period> <csv_filename>", file=sys.stderr)
        return 2

    seller, period, fname = sys.argv[1], sys.argv[2], sys.argv[3]
    year, month = int(period[:4]), int(period[5:7])
    import calendar
    last_day = calendar.monthrange(year, month)[1]
    start = f"{period}-01"
    end = f"{period}-{last_day:02d}"

    db = get_db()

    print(f"[1/3] Fetching ingester-created events ({seller} {period})...")
    all_refs: list[str] = []
    page = 0
    while True:
        r = db.table("payment_events").select("id,reference_id").eq(
            "seller_slug", seller
        ).in_("event_type", ["expense_captured", "expense_classified"]).gte(
            "competencia_date", start
        ).lte("competencia_date", end).range(page * 1000, (page + 1) * 1000 - 1).execute()
        if not r.data:
            break
        for row in r.data:
            if ":" in (row.get("reference_id") or ""):
                all_refs.append(row["id"])
        if len(r.data) < 1000:
            break
        page += 1
    print(f"    found {len(all_refs)} ingester events to delete")

    print("[2/3] Deleting in batches of 500...")
    for i in range(0, len(all_refs), 500):
        chunk = all_refs[i : i + 500]
        db.table("payment_events").delete().in_("id", chunk).execute()
        print(f"    deleted {min(i + 500, len(all_refs))}/{len(all_refs)}")

    print(f"[3/3] Re-ingesting extrato ({fname})...")
    path = PROJECT_ROOT / "testes" / "data" / "extratos" / fname
    csv_text = path.read_text(encoding="utf-8-sig")
    res = await ingest_extrato_from_csv(seller, csv_text, period)
    for k, v in res.items():
        if k == "by_type":
            continue
        print(f"    {k}: {v}")
    print(f"    by_type: {res.get('by_type')}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
