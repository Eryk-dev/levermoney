"""
Etapa 2: Classify non-order payments from cached data into mp_expenses.

Reads the payment cache JSON, filters payments without order.id,
and calls classify_non_order_payment() for each one.

Usage:
    python3 testes/classify_non_orders.py [--seller 141air] [--month jan2026] [--dry-run]

This script WRITES to mp_expenses in Supabase (unless --dry-run).
"""
import argparse
import asyncio
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.services.expense_classifier import classify_non_order_payment, _classify
from app.db.supabase import get_db


CACHE_FILES = {
    ("141air", "jan2026"): "testes/data/cache_jan2026/141air_payments.json",
}


async def run(seller_slug: str, month: str, dry_run: bool = False):
    key = (seller_slug, month)
    if key not in CACHE_FILES:
        print(f"ERROR: No cache file for {key}")
        print(f"Available: {list(CACHE_FILES.keys())}")
        sys.exit(1)

    cache_path = PROJECT_ROOT / CACHE_FILES[key]
    if not cache_path.exists():
        print(f"ERROR: Cache file not found: {cache_path}")
        sys.exit(1)

    # Load cache
    with open(cache_path, encoding="utf-8") as f:
        cache = json.load(f)
    payments = cache["payments"]
    print(f"Loaded {len(payments)} payments from cache")

    # Filter non-order payments (no order.id)
    non_order = [p for p in payments if not (p.get("order") or {}).get("id")]
    print(f"Non-order payments: {len(non_order)}")

    # Filter by status (only approved matters for classification)
    approved = [p for p in non_order if p.get("status") == "approved"]
    print(f"Approved non-order: {len(approved)}")

    if dry_run:
        print("\n=== DRY RUN — showing classification without writing ===\n")
        skip_count = 0
        classify_count = 0
        by_type = {}
        for p in approved:
            expense_type, direction, category, auto_cat, desc = _classify(p)
            if direction == "skip":
                skip_count += 1
                continue
            classify_count += 1
            key = f"{expense_type} ({direction})"
            by_type.setdefault(key, []).append({
                "id": p["id"],
                "amount": p.get("transaction_amount", 0),
                "desc": desc[:80],
                "category": category,
            })

        print(f"Would skip: {skip_count}")
        print(f"Would classify: {classify_count}")
        print()
        for t, entries in sorted(by_type.items()):
            total = sum(e["amount"] for e in entries)
            print(f"  {len(entries):3d}  {t}  (R$ {total:,.2f})")
            for e in entries[:3]:
                print(f"       id={e['id']}  R$ {e['amount']:,.2f}  {e['desc']}")
            if len(entries) > 3:
                print(f"       ... and {len(entries) - 3} more")
        return

    # Write to Supabase
    db = get_db()
    classified = 0
    skipped = 0
    errors = 0

    for i, p in enumerate(approved, 1):
        try:
            result = await classify_non_order_payment(db, seller_slug, p)
            if result is None:
                skipped += 1
            else:
                classified += 1
        except Exception as e:
            errors += 1
            print(f"  ERROR on payment {p['id']}: {e}")

        if i % 20 == 0:
            print(f"  Progress: {i}/{len(approved)} (classified={classified}, skipped={skipped}, errors={errors})")

    print(f"\nDone! Classified: {classified}, Skipped: {skipped}, Errors: {errors}")
    print(f"Total non-order approved: {len(approved)}")


def main():
    parser = argparse.ArgumentParser(description="Classify non-order payments from cache")
    parser.add_argument("--seller", default="141air", help="Seller slug")
    parser.add_argument("--month", default="jan2026", help="Month key")
    parser.add_argument("--dry-run", action="store_true", help="Show classification without writing")
    args = parser.parse_args()

    asyncio.run(run(args.seller, args.month, args.dry_run))


if __name__ == "__main__":
    main()
