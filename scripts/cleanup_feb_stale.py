#!/usr/bin/env python3
"""Cleanup stale Feb mp_expense rows + re-ingest.

Rows to delete:
  - payment_events where seller='141air' AND event_type='expense_captured'
    AND reference_id='143104571692:lc' (wrong sign from pre-fix ingest).
  - Any companion expense_classified event for the same ref.

After deletion, re-ingests the Feb CSV so the new classifier rules take effect:
  • reembolso_pix_enviado
  • dinheiro_recebido_cancelado
  • liberacao_cancelada with CSV-sign-driven direction
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

    stale_refs = [
        "143104571692:lc",  # liberacao_cancelada had wrong sign (expense; should be income)
    ]

    for stale_ref in stale_refs:
        res = db.table("payment_events").select("id, reference_id, event_type").eq(
            "seller_slug", seller
        ).eq("reference_id", stale_ref).execute()
        rows = res.data or []
        if not rows:
            print(f"  no rows found for {stale_ref}")
            continue
        print(f"  deleting {len(rows)} rows for ref={stale_ref}")
        for row in rows:
            print(f"    id={row['id']} event_type={row['event_type']}")
        ids = [row["id"] for row in rows]
        db.table("payment_events").delete().in_("id", ids).execute()

    # Re-ingest Feb CSV with updated classifier rules
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
