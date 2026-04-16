#!/usr/bin/env python3
"""Dump summary of mp_expenses for 141air fev/2026 by expense_type."""
from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.db.supabase import get_db

db = get_db()

seller = "141air"
start = "2026-02-01"
end = "2026-02-28"

rows = db.table("mp_expenses").select(
    "expense_type, amount, date_approved, payment_id, expense_direction, external_reference"
).eq("seller_slug", seller).gte("date_approved", start).lte(
    "date_approved", end
).range(0, 4999).execute().data or []

print(f"Total mp_expenses rows for {seller} {start}..{end}: {len(rows)}")
by_type = defaultdict(lambda: {"count": 0, "amount": 0.0})
for r in rows:
    t = r["expense_type"]
    by_type[t]["count"] += 1
    by_type[t]["amount"] += float(r["amount"] or 0)

print()
print(f"{'expense_type':<36} {'count':>8} {'total_amount':>16}")
for t in sorted(by_type.keys()):
    v = by_type[t]
    print(f"{t:<36} {v['count']:>8} {v['amount']:>16,.2f}")

# Print full list by target refs
print()
print("=== Rows for orphan-suspect refs (in-memory filter) ===")
targets = {
    "142959458860", "141979194794", "143104571692",
    "142698519459", "146365338433", "144531559071",
    "146292225459", "142961208182", "139380061139",
    "140563976561", "142100582011", "141693604662",
    "144986831209",
}
for r in rows:
    raw_pid = str(r.get("payment_id") or "")
    base = raw_pid.split(":")[0]
    ext = str(r.get("external_reference") or "")
    if base in targets or ext in targets:
        print(f"  {r.get('date_approved'):<12} type={r['expense_type']:<28} "
              f"dir={r.get('expense_direction'):<10} amt={float(r['amount'] or 0):>12,.2f} "
              f"pid={raw_pid} ext_ref={ext}")

# Check those refs against mp_expenses globally (any date)
print()
print("=== Rows for those refs in ANY date ===")
for ref in sorted(targets):
    r = db.table("mp_expenses").select(
        "expense_type, amount, date_approved, payment_id, expense_direction, external_reference"
    ).eq("seller_slug", seller).or_(
        f"payment_id.eq.{ref},payment_id.like.{ref}:*"
    ).execute().data or []
    print(f"  ref {ref}: {len(r)} rows")
    for row in r:
        print(f"     {row.get('date_approved'):<12} type={row['expense_type']:<28} "
              f"dir={row.get('expense_direction'):<10} amt={float(row['amount'] or 0):>12,.2f} "
              f"pid={row.get('payment_id')} ext_ref={row.get('external_reference')}")
