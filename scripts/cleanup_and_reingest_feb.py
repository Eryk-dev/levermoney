#!/usr/bin/env python3
"""One-shot: delete stale pix_enviado duplicates (actually refunds) for 141air
and re-ingest the Feb extrato CSV with the updated classifier rules.

Stale rows are identified by:
    seller_slug='141air',
    event_type='expense_captured',
    metadata->>'expense_type'='pix_enviado',
    reference_id LIKE '*:pe:%'  (suffix :pe:N for N>=2 means it's a duplicate)

Those duplicates were historically mis-classified because the classifier used
substring match on "pix enviado" which matched "Reembolso de Pix enviado".
The fix: add a more specific rule first, delete stale dupes, re-ingest.
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
    seller = "141air"
    month = "2026-02"
    db = get_db()

    # 1. Delete stale :pe:N duplicates. The FIRST occurrence (:pe) is the real
    # outgoing pix; any subsequent :pe:N rows on the same ref are duplicates
    # that should have been classified as reembolso_pix_enviado.
    res = db.table("payment_events").select("id, reference_id").eq(
        "seller_slug", seller
    ).eq("event_type", "expense_captured").like(
        "reference_id", "%:pe:%"
    ).execute()
    stale_rows = res.data or []
    print(f"Found {len(stale_rows)} stale :pe:N rows for {seller}")
    for row in stale_rows:
        print(f"  deleting id={row['id']} ref={row['reference_id']}")

    if stale_rows:
        ids_to_delete = [row["id"] for row in stale_rows]
        del_res = db.table("payment_events").delete().in_("id", ids_to_delete).execute()
        print(f"Deleted {len(del_res.data or [])} rows")

    # Also delete any companion expense_classified events for the same composite refs
    if stale_rows:
        stale_refs = list({row["reference_id"] for row in stale_rows})
        cls_res = db.table("payment_events").select("id, reference_id").eq(
            "seller_slug", seller
        ).eq("event_type", "expense_classified").in_(
            "reference_id", stale_refs
        ).execute()
        cls_rows = cls_res.data or []
        print(f"Found {len(cls_rows)} companion expense_classified rows to delete")
        if cls_rows:
            cls_ids = [r["id"] for r in cls_rows]
            db.table("payment_events").delete().in_("id", cls_ids).execute()

    # 2. Re-ingest Feb CSV with updated classifier rules
    csv_path = PROJECT_ROOT / "testes" / "data" / "extratos" / "extrato fevereiro 141Air.csv"
    csv_text = csv_path.read_text(encoding="utf-8-sig")
    print(f"\nRe-ingesting {csv_path}")
    result = await ingest_extrato_from_csv(seller, csv_text, month)
    print(f"Re-ingestion result:")
    print(f"  total_lines:      {result.get('total_lines')}")
    print(f"  skipped_internal: {result.get('skipped_internal')}")
    print(f"  already_covered:  {result.get('already_covered')}")
    print(f"  newly_ingested:   {result.get('newly_ingested')}")
    print(f"  errors:           {result.get('errors')}")
    print(f"  by_type:          {result.get('by_type')}")

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
