#!/usr/bin/env python3
"""
Repair payments.net_amount for partially_refunded rows.

Why:
- MP's transaction_details.net_received_amount can represent the pre-refund net.
- For partially_refunded payments, the effective released cash is lower.
- We align net_amount with the effective value used for daily cash reconciliation.

Usage:
  python3 testes/repair_partial_refund_net_amount.py --seller easy-utilidades --dry-run
  python3 testes/repair_partial_refund_net_amount.py --seller easy-utilidades --apply
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Iterable
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.db.supabase import get_db
from app.services.processor import _compute_effective_net_amount


def _iter_payments(db, seller_slug: str, page_size: int = 1000) -> Iterable[dict]:
    start = 0
    while True:
        rows = (
            db.table("payments")
            .select("id, seller_slug, ml_payment_id, net_amount, raw_payment")
            .eq("seller_slug", seller_slug)
            .range(start, start + page_size - 1)
            .execute()
            .data
            or []
        )
        for row in rows:
            yield row
        if len(rows) < page_size:
            break
        start += page_size


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--seller", required=True, help="seller slug")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="only preview")
    mode.add_argument("--apply", action="store_true", help="persist updates")
    args = parser.parse_args()

    db = get_db()

    scanned = 0
    candidates = 0
    updates = []

    for row in _iter_payments(db, args.seller):
        scanned += 1
        raw = row.get("raw_payment") or {}
        status_detail = str(raw.get("status_detail") or "").lower()
        if status_detail != "partially_refunded":
            continue
        candidates += 1

        current = float(row.get("net_amount") or 0)
        effective = float(_compute_effective_net_amount(raw))
        if abs(current - effective) < 0.01:
            continue

        updates.append(
            {
                "id": row["id"],
                "ml_payment_id": row.get("ml_payment_id"),
                "current": round(current, 2),
                "effective": round(effective, 2),
                "delta": round(effective - current, 2),
            }
        )

    print(f"seller={args.seller}")
    print(f"scanned={scanned}")
    print(f"partially_refunded_candidates={candidates}")
    print(f"needs_update={len(updates)}")

    for u in updates[:50]:
        print(
            f"payment_id={u['ml_payment_id']} "
            f"net_amount {u['current']:.2f} -> {u['effective']:.2f} "
            f"(delta {u['delta']:.2f})"
        )

    if args.dry_run:
        return

    for u in updates:
        (
            db.table("payments")
            .update({"net_amount": u["effective"]})
            .eq("id", u["id"])
            .execute()
        )
    print(f"updated={len(updates)}")


if __name__ == "__main__":
    main()
