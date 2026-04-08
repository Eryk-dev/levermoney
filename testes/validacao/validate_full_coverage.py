"""
Extrato full coverage validator — measures how many extrato lines are
covered by payment_events + mp_expenses in Supabase.

Usage:
    python3 testes/validate_full_coverage.py [--seller 141air] [--month jan2026]
    python3 testes/validate_full_coverage.py --help

Reads the extrato CSV offline, classifies every line, then queries Supabase
to determine coverage.  Produces a report at testes/reports/coverage_{seller}_{month}.txt.

This script is READ-ONLY — it does NOT modify any data in Supabase.
"""
import argparse
import os
import sys
from pathlib import Path
from datetime import datetime

# Ensure project root is on sys.path so we can import app modules
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.services.extrato_ingester import (
    _parse_account_statement,
    _classify_extrato_line,
    _resolve_check_payments,
    _CHECK_PAYMENTS,
    _EXPENSE_TYPE_ABBREV,
)
from app.db.supabase import get_db


# ── Configuration ─────────────────────────────────────────────────────────

EXTRATO_FILES = {
    ("141air", "jan2026"): "testes/data/extratos/extrato janeiro 141Air.csv",
    ("141air", "fev2026"): "testes/data/extratos/extrato fevereiro 141Air.csv",
}


# ── Supabase helpers ──────────────────────────────────────────────────────

def fetch_payment_ids_from_events(db, seller_slug: str) -> set[int]:
    """Fetch all payment_ids that have a sale_approved event."""
    ids: set[int] = set()
    page_start = 0
    page_limit = 1000
    while True:
        rows = (
            db.table("payment_events")
            .select("ml_payment_id")
            .eq("seller_slug", seller_slug)
            .eq("event_type", "sale_approved")
            .range(page_start, page_start + page_limit - 1)
            .execute()
            .data or []
        )
        for r in rows:
            ids.add(r["ml_payment_id"])
        if len(rows) < page_limit:
            break
        page_start += page_limit
    return ids


def fetch_expense_payment_ids(db, seller_slug: str) -> set[str]:
    """Fetch all payment_id values from mp_expenses for this seller."""
    ids: set[str] = set()
    page_start = 0
    page_limit = 1000
    while True:
        rows = (
            db.table("mp_expenses")
            .select("payment_id")
            .eq("seller_slug", seller_slug)
            .range(page_start, page_start + page_limit - 1)
            .execute()
            .data or []
        )
        for r in rows:
            ids.add(str(r["payment_id"]))
        if len(rows) < page_limit:
            break
        page_start += page_limit
    return ids


# ── Coverage analysis ─────────────────────────────────────────────────────

def analyze_coverage(
    extrato_path: str,
    seller_slug: str,
) -> dict:
    """Analyze extrato coverage against Supabase data.

    Returns a dict with full coverage report.
    """
    # 1. Parse extrato CSV
    csv_text = Path(extrato_path).read_text(encoding="utf-8")
    summary, transactions = _parse_account_statement(csv_text)

    total_lines = len(transactions)
    print(f"Parsed extrato: {total_lines} transaction lines")
    print(f"  Summary: initial={summary.get('initial_balance')}, "
          f"credits={summary.get('credits')}, debits={summary.get('debits')}, "
          f"final={summary.get('final_balance')}")

    # 2. Fetch Supabase data
    print("\nFetching Supabase data...")
    db = get_db()
    payment_ids = fetch_payment_ids_from_events(db, seller_slug)
    expense_ids = fetch_expense_payment_ids(db, seller_slug)
    # Build prefix set: for composite keys like "123456:ln", extract "123456"
    expense_ref_prefixes: set[str] = set()
    for eid in expense_ids:
        if ":" in eid:
            expense_ref_prefixes.add(eid.split(":")[0])
        else:
            expense_ref_prefixes.add(eid)
    print(f"  payment_events: {len(payment_ids)} unique payment IDs (sale_approved)")
    print(f"  mp_expenses: {len(expense_ids)} entries ({len(expense_ref_prefixes)} unique ref_ids)")

    # 3. Classify each line and check coverage
    results = {
        "skip": [],           # unconditional skip (internal)
        "covered_payment": [], # _CHECK_PAYMENTS resolved → in payment_events
        "covered_expense": [], # expense_type found in mp_expenses
        "gap_check": [],      # _CHECK_PAYMENTS → NOT in payment_events, NOT in mp_expenses
        "gap_expense": [],    # classified expense/income → NOT in mp_expenses
    }

    for tx in transactions:
        tx_type = tx["transaction_type"]
        ref_id = tx["reference_id"]
        amount = tx["amount"]

        expense_type, direction, ca_cat = _classify_extrato_line(tx_type)

        entry = {
            "date": tx["date"],
            "type": tx_type,
            "ref_id": ref_id,
            "amount": amount,
            "expense_type": expense_type,
            "direction": direction,
        }

        # Unconditional skip
        if expense_type is None:
            results["skip"].append(entry)
            continue

        # Conditional skip (_CHECK_PAYMENTS)
        if expense_type == _CHECK_PAYMENTS:
            # Check if ref_id is a valid payment_id in payment_events
            try:
                pid = int(ref_id)
                in_payments = pid in payment_ids
            except (ValueError, TypeError):
                in_payments = False

            if in_payments:
                results["covered_payment"].append(entry)
            else:
                # Check mp_expenses: plain ref_id OR composite key prefix
                # The ingester stores composite keys like "123456:ln"
                fallback_type, fallback_dir = _resolve_check_payments(tx_type)
                abbrev = _EXPENSE_TYPE_ABBREV.get(fallback_type, "xx")
                composite_abbrev = f"{ref_id}:{abbrev}"
                in_expenses = (
                    ref_id in expense_ids
                    or str(ref_id) in expense_ids
                    or ref_id in expense_ref_prefixes
                    or composite_abbrev in expense_ids
                )
                if in_expenses:
                    results["covered_expense"].append(entry)
                else:
                    fallback_type, fallback_dir = _resolve_check_payments(tx_type)
                    entry["fallback_type"] = fallback_type
                    entry["fallback_dir"] = fallback_dir
                    results["gap_check"].append(entry)
            continue

        # Regular expense/income classification
        # Check if this line is covered by mp_expenses
        # The payment_id in mp_expenses could be:
        #   - The ref_id directly (from classifier)
        #   - A composite key like "ref_id:dd" (from extrato ingester, abbreviated)
        abbrev = _EXPENSE_TYPE_ABBREV.get(expense_type, "xx")
        composite_abbrev = f"{ref_id}:{abbrev}"
        in_expenses = (
            ref_id in expense_ids
            or str(ref_id) in expense_ids
            or composite_abbrev in expense_ids
        )
        # Also check payment_events (some expense types like refunds may match)
        try:
            pid = int(ref_id)
            in_payments = pid in payment_ids
        except (ValueError, TypeError):
            in_payments = False

        if in_expenses or in_payments:
            results["covered_expense"].append(entry)
        else:
            results["gap_expense"].append(entry)

    return {
        "summary": summary,
        "total_lines": total_lines,
        "results": results,
    }


# ── Report generation ─────────────────────────────────────────────────────

def generate_report(analysis: dict, seller_slug: str, month: str) -> str:
    """Generate a human-readable coverage report."""
    r = analysis["results"]
    total = analysis["total_lines"]
    summary = analysis["summary"]

    skip_count = len(r["skip"])
    covered_payment = len(r["covered_payment"])
    covered_expense = len(r["covered_expense"])
    gap_check = len(r["gap_check"])
    gap_expense = len(r["gap_expense"])

    total_covered = skip_count + covered_payment + covered_expense
    total_gaps = gap_check + gap_expense
    coverage_pct = (total_covered / total * 100) if total > 0 else 0

    gap_amount = sum(e["amount"] for e in r["gap_check"]) + sum(e["amount"] for e in r["gap_expense"])

    lines = []
    lines.append("=" * 72)
    lines.append(f"EXTRATO COVERAGE REPORT — {seller_slug.upper()} {month.upper()}")
    lines.append(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append("=" * 72)
    lines.append("")

    # Summary
    lines.append("EXTRATO SUMMARY")
    lines.append(f"  Initial balance: R$ {summary.get('initial_balance', 0):,.2f}")
    lines.append(f"  Credits:         R$ {summary.get('credits', 0):,.2f}")
    lines.append(f"  Debits:          R$ {summary.get('debits', 0):,.2f}")
    lines.append(f"  Final balance:   R$ {summary.get('final_balance', 0):,.2f}")
    lines.append("")

    # Coverage
    lines.append("COVERAGE")
    lines.append(f"  Total lines:          {total}")
    lines.append(f"  Unconditional skips:  {skip_count}")
    lines.append(f"  Covered (payments):   {covered_payment}")
    lines.append(f"  Covered (expenses):   {covered_expense}")
    lines.append(f"  ─────────────────────────")
    lines.append(f"  Total covered:        {total_covered} ({coverage_pct:.1f}%)")
    lines.append(f"  GAPS:                 {total_gaps} ({100 - coverage_pct:.1f}%)")
    lines.append(f"  Gap amount:           R$ {gap_amount:,.2f}")
    lines.append("")

    # Skip breakdown
    if r["skip"]:
        skip_types: dict[str, int] = {}
        for e in r["skip"]:
            skip_types[e["type"]] = skip_types.get(e["type"], 0) + 1
        lines.append("SKIPS (internal, no financial impact)")
        for t, c in sorted(skip_types.items(), key=lambda x: -x[1]):
            lines.append(f"  {c:4d}  {t}")
        lines.append("")

    # Covered by payments breakdown
    if r["covered_payment"]:
        cp_types: dict[str, int] = {}
        for e in r["covered_payment"]:
            cp_types[e["type"]] = cp_types.get(e["type"], 0) + 1
        lines.append("COVERED BY PAYMENT_EVENTS")
        for t, c in sorted(cp_types.items(), key=lambda x: -x[1]):
            lines.append(f"  {c:4d}  {t}")
        lines.append("")

    # Covered by expenses breakdown
    if r["covered_expense"]:
        ce_types: dict[str, int] = {}
        for e in r["covered_expense"]:
            key = e.get("expense_type", "?")
            ce_types[key] = ce_types.get(key, 0) + 1
        lines.append("COVERED BY MP_EXPENSES")
        for t, c in sorted(ce_types.items(), key=lambda x: -x[1]):
            lines.append(f"  {c:4d}  {t}")
        lines.append("")

    # Gap: _CHECK_PAYMENTS not in DB
    if r["gap_check"]:
        lines.append(f"GAPS — CONDITIONAL CHECK ({gap_check} lines)")
        lines.append("  (Payment ref_id NOT in payment_events AND NOT in mp_expenses)")
        gc_types: dict[str, list] = {}
        for e in r["gap_check"]:
            key = e.get("fallback_type", "?")
            gc_types.setdefault(key, []).append(e)
        for t, entries in sorted(gc_types.items()):
            total_amt = sum(e["amount"] for e in entries)
            lines.append(f"  {len(entries):4d}  {t}  (R$ {total_amt:,.2f})")
            for e in entries[:5]:  # show first 5 samples
                lines.append(f"        ref={e['ref_id']}  amt={e['amount']:,.2f}  date={e['date']}  {e['type'][:60]}")
            if len(entries) > 5:
                lines.append(f"        ... and {len(entries) - 5} more")
        lines.append("")

    # Gap: classified expense/income not in DB
    if r["gap_expense"]:
        lines.append(f"GAPS — CLASSIFIED ({gap_expense} lines)")
        lines.append("  (Expense/income type NOT in mp_expenses)")
        ge_types: dict[str, list] = {}
        for e in r["gap_expense"]:
            key = e.get("expense_type", "?")
            ge_types.setdefault(key, []).append(e)
        for t, entries in sorted(ge_types.items()):
            total_amt = sum(e["amount"] for e in entries)
            lines.append(f"  {len(entries):4d}  {t}  (R$ {total_amt:,.2f})")
            for e in entries[:5]:
                lines.append(f"        ref={e['ref_id']}  amt={e['amount']:,.2f}  date={e['date']}  {e['type'][:60]}")
            if len(entries) > 5:
                lines.append(f"        ... and {len(entries) - 5} more")
        lines.append("")

    lines.append("=" * 72)
    return "\n".join(lines)


# ── Main ──────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Validate extrato coverage against Supabase")
    parser.add_argument("--seller", default="141air", help="Seller slug (default: 141air)")
    parser.add_argument("--month", default="jan2026", help="Month key (default: jan2026)")
    args = parser.parse_args()

    key = (args.seller, args.month)
    if key not in EXTRATO_FILES:
        print(f"ERROR: No extrato file configured for {key}")
        print(f"Available: {list(EXTRATO_FILES.keys())}")
        sys.exit(1)

    extrato_path = PROJECT_ROOT / EXTRATO_FILES[key]
    if not extrato_path.exists():
        print(f"ERROR: Extrato file not found: {extrato_path}")
        sys.exit(1)

    print(f"Validating coverage: {args.seller} / {args.month}")
    print(f"Extrato: {extrato_path}")
    print()

    analysis = analyze_coverage(str(extrato_path), args.seller)
    report = generate_report(analysis, args.seller, args.month)

    # Print to console
    print(report)

    # Save to file
    report_dir = PROJECT_ROOT / "testes" / "reports"
    report_dir.mkdir(exist_ok=True)
    report_path = report_dir / f"coverage_{args.seller}_{args.month}.txt"
    report_path.write_text(report, encoding="utf-8")
    print(f"\nReport saved to: {report_path}")


if __name__ == "__main__":
    main()
