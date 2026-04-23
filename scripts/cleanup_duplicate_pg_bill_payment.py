#!/usr/bin/env python3
"""Delete ':pg' pagamento_conta rows that duplicate existing bill_payment
(classifier) rows for the same ref. These duplicates only exist because the
earlier ERR-00xx resets re-ran extrato_ingester BEFORE classifier had
re-populated its rows.

Safe because: the bill_payment row (operation_type='regular_payment') already
captures the same cash event and maps to `pagamento_conta` category via
`_expense_type_to_category`.
"""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.db.supabase import get_db


def main() -> int:
    seller = "net-air"
    db = get_db()

    # 1. Find all bill_payment pids (classifier source) in Jan 2026
    bp = (
        db.table("mp_expenses")
        .select("payment_id")
        .eq("seller_slug", seller)
        .eq("expense_type", "bill_payment")
        .gte("date_approved", "2026-01-01")
        .lt("date_approved", "2026-02-01")
        .range(0, 5000)
        .execute()
    )
    bp_pids = {str(row["payment_id"]).split(":")[0] for row in (bp.data or [])}
    print(f"Found {len(bp_pids)} bill_payment pids in Jan 2026")

    # 2. For each such pid, delete the companion :pg row from payment_events.
    #    Also delete any :pg:N (multi-occurrence) for the same pid — those
    #    duplicate whatever the classifier/release covers.
    deleted = 0
    for pid in bp_pids:
        # delete reference_id = "{pid}:pg" and "{pid}:pg:N"
        res = (
            db.table("payment_events")
            .delete()
            .eq("seller_slug", seller)
            .like("reference_id", f"{pid}:pg%")
            .in_("event_type", ["expense_captured", "expense_classified"])
            .execute()
        )
        deleted += len(res.data or [])

    print(f"Deleted {deleted} duplicate :pg rows from payment_events")
    return 0


if __name__ == "__main__":
    sys.exit(main())
