"""
Etapa 3: Ingest extrato gap lines into mp_expenses using cached CSV.

Reuses the core ingester logic (classification, composite keys, dedup)
but reads from a local CSV instead of the ML API.

Usage:
    python3 testes/ingest_extrato_gaps.py [--seller 141air] [--month jan2026] [--dry-run]

This script WRITES to mp_expenses in Supabase (unless --dry-run).
"""
import argparse
import asyncio
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.services.extrato_ingester import (
    _parse_account_statement,
    _classify_extrato_line,
    _resolve_check_payments,
    _CHECK_PAYMENTS,
    _EXPENSE_TYPE_ABBREV,
    _build_expense_from_extrato,
    _batch_lookup_payment_ids,
    _batch_lookup_expense_payment_ids,
    _batch_lookup_expense_details,
    _batch_lookup_composite_expense_ids,
    _batch_lookup_refunded_payment_ids,
    _update_expense_amount_from_extrato,
    _fuzzy_match_expense,
)
from app.db.supabase import get_db


EXTRATO_FILES = {
    ("141air", "jan2026"): "testes/data/extratos/extrato janeiro 141Air.csv",
    ("141air", "fev2026"): "testes/data/extratos/extrato fevereiro 141Air.csv",
}


async def run(seller_slug: str, month: str, dry_run: bool = False):
    key = (seller_slug, month)
    if key not in EXTRATO_FILES:
        print(f"ERROR: No extrato file for {key}")
        print(f"Available: {list(EXTRATO_FILES.keys())}")
        sys.exit(1)

    extrato_path = PROJECT_ROOT / EXTRATO_FILES[key]
    if not extrato_path.exists():
        print(f"ERROR: File not found: {extrato_path}")
        sys.exit(1)

    # 1. Parse CSV
    csv_text = extrato_path.read_text(encoding="utf-8")
    summary, transactions = _parse_account_statement(csv_text)
    print(f"Parsed extrato: {len(transactions)} lines")
    print(f"  Summary: initial={summary.get('initial_balance')}, "
          f"credits={summary.get('credits')}, debits={summary.get('debits')}, "
          f"final={summary.get('final_balance')}")

    # 2. First-pass classification
    classified: list[tuple[dict, str, str, Optional[str], str]] = []
    stats = Counter()

    for tx in transactions:
        expense_type, direction, ca_category_uuid = _classify_extrato_line(
            tx["transaction_type"]
        )

        if expense_type is None and direction is None:
            stats["skipped_internal"] += 1
            continue

        if expense_type == _CHECK_PAYMENTS:
            payment_id_key = f"{tx['reference_id']}:cp"
        else:
            abbrev = _EXPENSE_TYPE_ABBREV.get(expense_type, "xx") if expense_type else "xx"
            payment_id_key = f"{tx['reference_id']}:{abbrev}"

        classified.append((tx, expense_type, direction, ca_category_uuid, payment_id_key))

    print(f"\nClassification: {len(classified)} gap candidates, {stats['skipped_internal']} unconditional skips")

    if not classified:
        print("Nothing to ingest.")
        return

    # 3. Batch lookups
    db = get_db()
    all_ref_ids = list({item[0]["reference_id"] for item in classified})

    print("Fetching DB state...")
    payment_ids_in_db = await _batch_lookup_payment_ids(db, seller_slug, all_ref_ids)
    expense_ids_in_db = _batch_lookup_expense_payment_ids(db, seller_slug, all_ref_ids)
    expense_details_in_db = _batch_lookup_expense_details(db, seller_slug, all_ref_ids)
    print(f"  payment_events: {len(payment_ids_in_db)} ref_ids found")
    print(f"  mp_expenses (plain): {len(expense_ids_in_db)} ref_ids found")

    # 4. Resolve _CHECK_PAYMENTS
    resolved: list[tuple[dict, str, str, Optional[str], str]] = []
    for tx, expense_type, direction, ca_category_uuid, payment_id_key in classified:
        if expense_type == _CHECK_PAYMENTS:
            ref_id = tx["reference_id"]
            if ref_id in payment_ids_in_db:
                stats["skipped_internal"] += 1
                continue
            fallback_type, fallback_dir = _resolve_check_payments(tx["transaction_type"])
            abbrev = _EXPENSE_TYPE_ABBREV.get(fallback_type, "xx")
            payment_id_key = f"{ref_id}:{abbrev}"
            resolved.append((tx, fallback_type, fallback_dir, None, payment_id_key))
        else:
            resolved.append((tx, expense_type, direction, ca_category_uuid, payment_id_key))

    classified = resolved

    # 5. Composite key lookup
    composite_keys = [item[4] for item in classified]
    composite_ids_in_db = _batch_lookup_composite_expense_ids(db, seller_slug, composite_keys)
    refunded_payment_ids_in_db = await _batch_lookup_refunded_payment_ids(db, seller_slug, all_ref_ids)

    print(f"  mp_expenses (composite): {len(composite_ids_in_db)} keys found")
    print(f"  refunded payments: {len(refunded_payment_ids_in_db)} ref_ids")
    print(f"  Lines to process: {len(classified)}")

    if dry_run:
        print("\n=== DRY RUN — showing what would be ingested ===\n")
        would_ingest: dict[str, list] = {}
        would_skip = 0

        for tx, expense_type, direction, ca_category_uuid, payment_id_key in classified:
            ref_id = tx["reference_id"]

            # Composite already exists
            if payment_id_key in composite_ids_in_db:
                would_skip += 1
                continue

            # Plain ref_id in payments
            if ref_id in payment_ids_in_db:
                if expense_type == "debito_divida_disputa":
                    if ref_id in refunded_payment_ids_in_db:
                        would_skip += 1
                        continue
                elif expense_type in ("reembolso_disputa", "reembolso_generico",
                                      "entrada_dinheiro", "dinheiro_retido",
                                      "liberacao_cancelada", "debito_envio_ml",
                                      "bonus_envio", "debito_troca"):
                    pass
                else:
                    would_skip += 1
                    continue

            # Plain ref_id in mp_expenses
            if ref_id in expense_ids_in_db:
                detail = expense_details_in_db.get(ref_id)
                if detail:
                    extrato_amount = abs(tx["amount"])
                    if abs(detail["amount"] - extrato_amount) >= 0.01:
                        would_ingest.setdefault("amount_update", []).append({
                            "key": payment_id_key,
                            "old_amount": detail["amount"],
                            "new_amount": extrato_amount,
                        })
                    else:
                        would_skip += 1
                    continue

            # Fuzzy match for faturas_ml / collection
            if expense_type in ("faturas_ml", "collection"):
                extrato_amount = abs(tx["amount"])
                if _fuzzy_match_expense(db, seller_slug, extrato_amount, tx["date"],
                                        ["faturas_ml", "collection"]):
                    would_skip += 1
                    continue

            would_ingest.setdefault(expense_type, []).append({
                "key": payment_id_key,
                "amount": tx["amount"],
                "date": tx["date"],
                "type_raw": tx["transaction_type"][:60],
            })

        print(f"Would skip (already covered): {would_skip}")
        total_ingest = sum(len(v) for v in would_ingest.items())
        print(f"Would ingest: {total_ingest}")
        for t, entries in sorted(would_ingest.items()):
            total_amt = sum(e.get("amount", 0) for e in entries)
            print(f"  {len(entries):3d}  {t}  (R$ {total_amt:,.2f})")
            for e in entries[:3]:
                if "old_amount" in e:
                    print(f"       {e['key']}  R$ {e['old_amount']:.2f} → R$ {e['new_amount']:.2f}")
                else:
                    print(f"       {e['key']}  R$ {e['amount']:,.2f}  {e['date']}  {e.get('type_raw', '')}")
            if len(entries) > 3:
                print(f"       ... and {len(entries) - 3} more")
        return

    # 6. Upsert into mp_expenses (same logic as ingest_extrato_for_seller)
    by_type: Counter = Counter()

    for tx, expense_type, direction, ca_category_uuid, payment_id_key in classified:
        ref_id = tx["reference_id"]

        # a. Composite key already ingested
        if payment_id_key in composite_ids_in_db:
            stats["already_covered"] += 1
            continue

        # b. Plain ref_id in payment_events
        if ref_id in payment_ids_in_db:
            if expense_type == "debito_divida_disputa":
                if ref_id in refunded_payment_ids_in_db:
                    stats["already_covered"] += 1
                    continue
            elif expense_type in ("reembolso_disputa", "reembolso_generico",
                                  "entrada_dinheiro", "dinheiro_retido",
                                  "liberacao_cancelada", "debito_envio_ml",
                                  "bonus_envio", "debito_troca"):
                pass
            else:
                stats["already_covered"] += 1
                continue

        # c. Plain ref_id in mp_expenses — IOF amount correction
        if ref_id in expense_ids_in_db:
            detail = expense_details_in_db.get(ref_id)
            if detail:
                extrato_amount = abs(tx["amount"])
                if abs(detail["amount"] - extrato_amount) >= 0.01:
                    updated = _update_expense_amount_from_extrato(
                        db, seller_slug, detail, extrato_amount, ref_id,
                    )
                    if updated:
                        stats["amount_updated"] += 1
                    else:
                        stats["already_covered"] += 1
                else:
                    stats["already_covered"] += 1
                continue

        # c2. Fuzzy dedup for faturas_ml / collection
        if expense_type in ("faturas_ml", "collection"):
            extrato_amount = abs(tx["amount"])
            if _fuzzy_match_expense(db, seller_slug, extrato_amount, tx["date"],
                                    ["faturas_ml", "collection"]):
                stats["already_covered"] += 1
                continue

        # d. Build and upsert
        row = _build_expense_from_extrato(
            tx, seller_slug, expense_type, direction, ca_category_uuid, payment_id_key
        )

        existing_check = (
            db.table("mp_expenses")
            .select("id, status")
            .eq("seller_slug", seller_slug)
            .eq("payment_id", payment_id_key)
            .execute()
        )

        try:
            if existing_check.data:
                existing_row = existing_check.data[0]
                if existing_row.get("status") == "exported":
                    stats["already_covered"] += 1
                    continue
                db.table("mp_expenses").update(row).eq("id", existing_row["id"]).execute()
                stats["already_covered"] += 1
            else:
                row["created_at"] = datetime.now(timezone.utc).isoformat()
                db.table("mp_expenses").insert(row).execute()
                stats["newly_ingested"] += 1
                by_type[expense_type] += 1
                print(f"  Ingested: {payment_id_key} type={expense_type} amount={abs(tx['amount']):.2f}")

        except Exception as exc:
            error_str = str(exc).lower()
            if "duplicate" in error_str or "unique" in error_str:
                stats["already_covered"] += 1
            else:
                stats["errors"] += 1
                print(f"  ERROR: {payment_id_key} — {exc}")

    print(f"\n{'='*60}")
    print(f"RESULTS")
    print(f"  Total lines:       {len(transactions)}")
    print(f"  Skipped (internal): {stats['skipped_internal']}")
    print(f"  Already covered:   {stats['already_covered']}")
    print(f"  Amount updated:    {stats.get('amount_updated', 0)}")
    print(f"  Newly ingested:    {stats['newly_ingested']}")
    print(f"  Errors:            {stats['errors']}")
    print(f"  By type:           {dict(by_type)}")


def main():
    parser = argparse.ArgumentParser(description="Ingest extrato gaps from cached CSV")
    parser.add_argument("--seller", default="141air", help="Seller slug")
    parser.add_argument("--month", default="jan2026", help="Month key")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be ingested")
    args = parser.parse_args()

    asyncio.run(run(args.seller, args.month, args.dry_run))


if __name__ == "__main__":
    main()
