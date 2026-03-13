"""
Day-by-day extrato reconciliation — validates that financial amounts match
between the extrato CSV and the system (payment_events + mp_expenses).

Three levels of validation:
  1. Extrato integrity (offline): running balance, credits/debits sums
  2. Per-line amount match: extrato amount vs system amount
  3. Daily balance table: tracked + untracked = extrato total per day

Usage:
    python3 testes/validate_daily_balance.py [--seller 141air] [--month jan2026]

This script is READ-ONLY — it does NOT modify any data in Supabase.
"""
import argparse
import os
import sys
from collections import defaultdict
from pathlib import Path

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


EXTRATO_FILES = {
    ("141air", "jan2026"): "testes/data/extratos/extrato janeiro 141Air.csv",
    ("141air", "fev2026"): "testes/data/extratos/extrato fevereiro 141Air.csv",
}


# ── Supabase helpers ──────────────────────────────────────────────────────


def fetch_payment_amounts(db, seller_slug: str) -> dict[int, float]:
    """Fetch net release amount per payment from payment_events.

    The net released = sum of all signed_amounts for that payment
    (sale_approved - fee_charged - shipping_charged).
    But the extrato "Liberação de dinheiro" shows the NET amount,
    which corresponds to the transaction_details.net_received_amount
    stored in sale_approved metadata.

    For simplicity, we fetch sale_approved events and look at the
    metadata net_received_amount. If not available, we compute
    sum of all events for that payment.
    """
    amounts: dict[int, float] = {}
    page_start = 0
    page_limit = 1000
    while True:
        rows = (
            db.table("payment_events")
            .select("ml_payment_id, signed_amount, metadata")
            .eq("seller_slug", seller_slug)
            .eq("event_type", "sale_approved")
            .range(page_start, page_start + page_limit - 1)
            .execute()
            .data or []
        )
        for r in rows:
            pid = r["ml_payment_id"]
            meta = r.get("metadata") or {}
            # net_received is what actually gets released to the account
            net = meta.get("net_received_amount")
            if net is not None:
                amounts[pid] = float(net)
            else:
                # Fallback: use gross (signed_amount of sale_approved)
                amounts[pid] = float(r["signed_amount"])
        if len(rows) < page_limit:
            break
        page_start += page_limit
    return amounts


def fetch_expense_amounts(db, seller_slug: str) -> dict[str, dict]:
    """Fetch amount and direction for all mp_expenses.

    Returns dict mapping payment_id -> {amount, direction, expense_type}.
    """
    expenses: dict[str, dict] = {}
    page_start = 0
    page_limit = 1000
    while True:
        rows = (
            db.table("mp_expenses")
            .select("payment_id, amount, expense_direction, expense_type")
            .eq("seller_slug", seller_slug)
            .range(page_start, page_start + page_limit - 1)
            .execute()
            .data or []
        )
        for r in rows:
            expenses[str(r["payment_id"])] = {
                "amount": float(r["amount"]),
                "direction": r["expense_direction"],
                "expense_type": r.get("expense_type"),
            }
        if len(rows) < page_limit:
            break
        page_start += page_limit
    return expenses


# ── Level 1: Extrato integrity ───────────────────────────────────────────


def validate_integrity(summary: dict, transactions: list[dict]) -> list[str]:
    """Validate internal consistency of the extrato CSV."""
    errors = []
    initial = summary.get("initial_balance", 0)
    final = summary.get("final_balance", 0)
    credits = summary.get("credits", 0)
    debits = summary.get("debits", 0)

    # Check: initial + sum(amounts) == final
    total_amount = sum(tx["amount"] for tx in transactions)
    expected_final = initial + total_amount
    if abs(expected_final - final) >= 0.01:
        errors.append(
            f"Balance mismatch: initial ({initial:.2f}) + sum ({total_amount:.2f}) "
            f"= {expected_final:.2f}, expected final = {final:.2f}, "
            f"diff = {expected_final - final:.2f}"
        )

    # Check: sum(positives) == credits, sum(negatives) == debits
    sum_pos = sum(tx["amount"] for tx in transactions if tx["amount"] > 0)
    sum_neg = sum(tx["amount"] for tx in transactions if tx["amount"] < 0)
    if abs(sum_pos - credits) >= 0.01:
        errors.append(f"Credits mismatch: sum_positive={sum_pos:.2f} vs header={credits:.2f}")
    if abs(sum_neg - debits) >= 0.01:
        errors.append(f"Debits mismatch: sum_negative={sum_neg:.2f} vs header={debits:.2f}")

    # Check: running balance line by line
    running = initial
    balance_errors = 0
    for i, tx in enumerate(transactions):
        running += tx["amount"]
        expected = tx["balance"]
        if abs(running - expected) >= 0.01:
            balance_errors += 1
            if balance_errors <= 3:
                errors.append(
                    f"Running balance error line {i+1}: "
                    f"computed={running:.2f} vs CSV={expected:.2f} "
                    f"(date={tx['date']} type={tx['transaction_type'][:40]})"
                )
    if balance_errors > 3:
        errors.append(f"  ... and {balance_errors - 3} more running balance errors")

    return errors


# ── Level 2: Per-line reconciliation ──────────────────────────────────────


def reconcile_lines(
    transactions: list[dict],
    payment_amounts: dict[int, float],
    expense_amounts: dict[str, dict],
) -> list[dict]:
    """Classify each extrato line and match amounts with system records.

    Returns a list of dicts, one per transaction, with:
        date, amount, category (skip/payment/expense), source_key,
        system_amount, diff, expense_type
    """
    payment_ids_set = set(payment_amounts.keys())

    # Build expense prefix set for coverage check
    expense_ref_prefixes: set[str] = set()
    for eid in expense_amounts:
        if ":" in eid:
            expense_ref_prefixes.add(eid.split(":")[0])
        else:
            expense_ref_prefixes.add(eid)

    results = []
    for tx in transactions:
        ref_id = tx["reference_id"]
        extrato_amount = tx["amount"]
        tx_type = tx["transaction_type"]

        expense_type, direction, ca_cat = _classify_extrato_line(tx_type)

        entry = {
            "date": tx["date"],
            "amount": extrato_amount,
            "tx_type": tx_type,
            "ref_id": ref_id,
            "category": None,       # skip, payment, expense
            "source_key": None,     # key used to find in system
            "system_amount": None,  # amount from system (signed, same convention as extrato)
            "diff": 0.0,
            "expense_type": expense_type,
        }

        # Unconditional skip
        if expense_type is None:
            entry["category"] = "skip"
            entry["system_amount"] = extrato_amount  # no system record, use extrato
            results.append(entry)
            continue

        # _CHECK_PAYMENTS: check payment_events first, then mp_expenses
        if expense_type == _CHECK_PAYMENTS:
            try:
                pid = int(ref_id)
                if pid in payment_ids_set:
                    entry["category"] = "payment"
                    entry["source_key"] = str(pid)
                    # The extrato amount IS the net released — it's the truth
                    # System stores net_received_amount in sale_approved metadata
                    sys_amt = payment_amounts[pid]
                    entry["system_amount"] = sys_amt  # positive (income)
                    entry["diff"] = extrato_amount - sys_amt
                    results.append(entry)
                    continue
            except (ValueError, TypeError):
                pass

            # Check mp_expenses (composite key)
            fallback_type, fallback_dir = _resolve_check_payments(tx_type)
            abbrev = _EXPENSE_TYPE_ABBREV.get(fallback_type, "xx")
            composite_key = f"{ref_id}:{abbrev}"
            if composite_key in expense_amounts:
                exp = expense_amounts[composite_key]
                entry["category"] = "expense"
                entry["source_key"] = composite_key
                entry["expense_type"] = fallback_type
                # Reconstruct signed amount from stored absolute
                sys_amt = exp["amount"] if exp["direction"] == "income" else -exp["amount"]
                entry["system_amount"] = sys_amt
                entry["diff"] = extrato_amount - sys_amt
                results.append(entry)
                continue

            # Check plain ref_id
            if ref_id in expense_ref_prefixes or ref_id in expense_amounts:
                entry["category"] = "expense"
                entry["source_key"] = ref_id
                if ref_id in expense_amounts:
                    exp = expense_amounts[ref_id]
                    sys_amt = exp["amount"] if exp["direction"] == "income" else -exp["amount"]
                    entry["system_amount"] = sys_amt
                    entry["diff"] = extrato_amount - sys_amt
                else:
                    entry["system_amount"] = extrato_amount
                results.append(entry)
                continue

            # Not found — gap
            entry["category"] = "gap"
            entry["system_amount"] = 0
            entry["diff"] = extrato_amount
            results.append(entry)
            continue

        # Regular classified expense/income
        abbrev = _EXPENSE_TYPE_ABBREV.get(expense_type, "xx")
        composite_key = f"{ref_id}:{abbrev}"

        if composite_key in expense_amounts:
            exp = expense_amounts[composite_key]
            entry["category"] = "expense"
            entry["source_key"] = composite_key
            sys_amt = exp["amount"] if exp["direction"] == "income" else -exp["amount"]
            entry["system_amount"] = sys_amt
            entry["diff"] = extrato_amount - sys_amt
        elif ref_id in expense_amounts:
            exp = expense_amounts[ref_id]
            entry["category"] = "expense"
            entry["source_key"] = ref_id
            sys_amt = exp["amount"] if exp["direction"] == "income" else -exp["amount"]
            entry["system_amount"] = sys_amt
            entry["diff"] = extrato_amount - sys_amt
        else:
            # Check payment_events
            try:
                pid = int(ref_id)
                if pid in payment_ids_set:
                    entry["category"] = "payment"
                    entry["source_key"] = str(pid)
                    sys_amt = payment_amounts[pid]
                    entry["system_amount"] = sys_amt
                    entry["diff"] = extrato_amount - sys_amt
                else:
                    entry["category"] = "gap"
                    entry["system_amount"] = 0
                    entry["diff"] = extrato_amount
            except (ValueError, TypeError):
                entry["category"] = "gap"
                entry["system_amount"] = 0
                entry["diff"] = extrato_amount

        results.append(entry)

    return results


# ── Level 3: Daily balance table ──────────────────────────────────────────


def build_daily_table(
    initial_balance: float,
    line_results: list[dict],
) -> list[dict]:
    """Aggregate line results into daily balance rows."""
    # Group by date
    by_day: dict[str, list[dict]] = defaultdict(list)
    for lr in line_results:
        by_day[lr["date"]].append(lr)

    days = sorted(by_day.keys())
    table = []
    running = initial_balance

    for day in days:
        lines = by_day[day]

        extrato_credits = sum(lr["amount"] for lr in lines if lr["amount"] > 0)
        extrato_debits = sum(lr["amount"] for lr in lines if lr["amount"] < 0)
        extrato_net = extrato_credits + extrato_debits

        tracked = sum(lr["amount"] for lr in lines if lr["category"] in ("payment", "expense"))
        untracked = sum(lr["amount"] for lr in lines if lr["category"] == "skip")
        gaps = sum(lr["amount"] for lr in lines if lr["category"] == "gap")
        system_total = tracked + untracked + gaps

        gap = extrato_net - system_total
        running += extrato_net

        # Per-line amount divergences
        divergences = [lr for lr in lines if abs(lr["diff"]) >= 0.01]

        table.append({
            "date": day,
            "lines": len(lines),
            "extrato_credits": extrato_credits,
            "extrato_debits": extrato_debits,
            "extrato_net": extrato_net,
            "tracked": tracked,
            "untracked": untracked,
            "gaps": gaps,
            "system_total": system_total,
            "gap": gap,
            "balance": running,
            "divergences": divergences,
        })

    return table


# ── Report ────────────────────────────────────────────────────────────────


def generate_report(
    summary: dict,
    integrity_errors: list[str],
    line_results: list[dict],
    daily_table: list[dict],
    seller_slug: str,
    month: str,
) -> str:
    from datetime import datetime

    lines = []
    lines.append("=" * 80)
    lines.append(f"DAILY BALANCE RECONCILIATION — {seller_slug.upper()} {month.upper()}")
    lines.append(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append("=" * 80)

    # ── Level 1: Integrity ──
    lines.append("")
    lines.append("LEVEL 1: EXTRATO INTEGRITY")
    lines.append(f"  Initial balance: R$ {summary.get('initial_balance', 0):,.2f}")
    lines.append(f"  Credits:         R$ {summary.get('credits', 0):,.2f}")
    lines.append(f"  Debits:          R$ {summary.get('debits', 0):,.2f}")
    lines.append(f"  Final balance:   R$ {summary.get('final_balance', 0):,.2f}")
    if integrity_errors:
        lines.append(f"  ERRORS: {len(integrity_errors)}")
        for e in integrity_errors:
            lines.append(f"    !! {e}")
    else:
        lines.append("  Status: OK (running balance consistent)")

    # ── Level 2: Per-line summary ──
    lines.append("")
    lines.append("LEVEL 2: PER-LINE AMOUNT MATCH")
    total = len(line_results)
    by_cat = defaultdict(int)
    for lr in line_results:
        by_cat[lr["category"]] += 1

    lines.append(f"  Total lines:    {total}")
    lines.append(f"  Payments:       {by_cat.get('payment', 0)}")
    lines.append(f"  Expenses:       {by_cat.get('expense', 0)}")
    lines.append(f"  Skips:          {by_cat.get('skip', 0)}")
    lines.append(f"  Gaps:           {by_cat.get('gap', 0)}")

    # Amount divergences (extrato vs system)
    divergences = [lr for lr in line_results if abs(lr["diff"]) >= 0.01]
    lines.append(f"  Amount divergences: {len(divergences)}")
    if divergences:
        total_div = sum(lr["diff"] for lr in divergences)
        lines.append(f"  Total divergence:   R$ {total_div:,.2f}")
        lines.append("")
        lines.append("  DIVERGENCES (extrato_amount != system_amount):")
        for d in divergences[:20]:
            lines.append(
                f"    {d['date']}  ref={d['ref_id']}  "
                f"extrato=R$ {d['amount']:,.2f}  system=R$ {d.get('system_amount', 0):,.2f}  "
                f"diff=R$ {d['diff']:,.2f}  [{d['category']}:{d.get('expense_type', '')}]"
            )
        if len(divergences) > 20:
            lines.append(f"    ... and {len(divergences) - 20} more")
    else:
        lines.append("  Status: OK (all amounts match)")

    # ── Level 3: Daily table ──
    lines.append("")
    lines.append("LEVEL 3: DAILY BALANCE RECONCILIATION")
    lines.append("")
    header = (
        f"{'DATE':<12} {'LINES':>5} {'EXTRATO NET':>14} "
        f"{'TRACKED':>14} {'UNTRACKED':>14} {'GAPS':>10} "
        f"{'SYST TOTAL':>14} {'GAP':>10} {'BALANCE':>14}"
    )
    lines.append(header)
    lines.append("-" * len(header))

    total_gap = 0
    for row in daily_table:
        total_gap += row["gap"]
        flag = " !!" if abs(row["gap"]) >= 0.01 else ""
        lines.append(
            f"{row['date']:<12} {row['lines']:>5} "
            f"{row['extrato_net']:>14,.2f} "
            f"{row['tracked']:>14,.2f} {row['untracked']:>14,.2f} "
            f"{row['gaps']:>10,.2f} "
            f"{row['system_total']:>14,.2f} "
            f"{row['gap']:>10,.2f} "
            f"{row['balance']:>14,.2f}{flag}"
        )

    lines.append("-" * len(header))

    # Totals
    total_extrato = sum(r["extrato_net"] for r in daily_table)
    total_tracked = sum(r["tracked"] for r in daily_table)
    total_untracked = sum(r["untracked"] for r in daily_table)
    total_gaps_amount = sum(r["gaps"] for r in daily_table)
    total_system = sum(r["system_total"] for r in daily_table)
    final_balance = daily_table[-1]["balance"] if daily_table else 0

    lines.append(
        f"{'TOTAL':<12} {total:>5} "
        f"{total_extrato:>14,.2f} "
        f"{total_tracked:>14,.2f} {total_untracked:>14,.2f} "
        f"{total_gaps_amount:>10,.2f} "
        f"{total_system:>14,.2f} "
        f"{total_gap:>10,.2f} "
        f"{final_balance:>14,.2f}"
    )

    lines.append("")
    lines.append("SUMMARY")
    lines.append(f"  Extrato final balance: R$ {summary.get('final_balance', 0):,.2f}")
    lines.append(f"  Computed balance:      R$ {final_balance:,.2f}")
    balance_diff = final_balance - summary.get("final_balance", 0)
    lines.append(f"  Balance difference:    R$ {balance_diff:,.2f}")
    lines.append(f"  Total daily gaps:      R$ {total_gap:,.2f}")

    if abs(balance_diff) < 0.01 and abs(total_gap) < 0.01 and len(divergences) == 0:
        lines.append("")
        lines.append("  RESULT: RECONCILIATION OK — all amounts match, balance verified")
    elif abs(balance_diff) < 0.01 and abs(total_gap) < 0.01:
        lines.append("")
        lines.append(f"  RESULT: BALANCE OK — {len(divergences)} per-line divergences (see above)")
    else:
        lines.append("")
        lines.append("  RESULT: DIVERGENCE FOUND — investigate gaps above")

    lines.append("")
    lines.append("=" * 80)
    return "\n".join(lines)


# ── Main ──────────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(description="Day-by-day extrato reconciliation")
    parser.add_argument("--seller", default="141air", help="Seller slug")
    parser.add_argument("--month", default="jan2026", help="Month key")
    args = parser.parse_args()

    key = (args.seller, args.month)
    if key not in EXTRATO_FILES:
        print(f"ERROR: No extrato file for {key}")
        print(f"Available: {list(EXTRATO_FILES.keys())}")
        sys.exit(1)

    extrato_path = PROJECT_ROOT / EXTRATO_FILES[key]
    if not extrato_path.exists():
        print(f"ERROR: File not found: {extrato_path}")
        sys.exit(1)

    print(f"Reconciling: {args.seller} / {args.month}")
    print(f"Extrato: {extrato_path}")
    print()

    # 1. Parse
    csv_text = extrato_path.read_text(encoding="utf-8")
    summary, transactions = _parse_account_statement(csv_text)
    print(f"Parsed: {len(transactions)} lines")

    # 2. Level 1: Integrity
    print("\nLevel 1: Checking extrato integrity...")
    integrity_errors = validate_integrity(summary, transactions)
    if integrity_errors:
        print(f"  ERRORS: {len(integrity_errors)}")
        for e in integrity_errors:
            print(f"    {e}")
    else:
        print("  OK")

    # 3. Fetch Supabase data
    print("\nFetching Supabase data...")
    db = get_db()
    payment_amounts = fetch_payment_amounts(db, args.seller)
    expense_amounts = fetch_expense_amounts(db, args.seller)
    print(f"  payment_events: {len(payment_amounts)} payments with amounts")
    print(f"  mp_expenses: {len(expense_amounts)} entries with amounts")

    # 4. Level 2: Per-line reconciliation
    print("\nLevel 2: Per-line reconciliation...")
    line_results = reconcile_lines(transactions, payment_amounts, expense_amounts)
    divergences = [lr for lr in line_results if abs(lr["diff"]) >= 0.01]
    print(f"  Divergences: {len(divergences)}")

    # 5. Level 3: Daily table
    print("\nLevel 3: Building daily balance table...")
    daily_table = build_daily_table(summary.get("initial_balance", 0), line_results)

    # 6. Report
    report = generate_report(
        summary, integrity_errors, line_results, daily_table,
        args.seller, args.month,
    )
    print()
    print(report)

    # Save
    report_dir = PROJECT_ROOT / "testes" / "reports"
    report_dir.mkdir(exist_ok=True)
    report_path = report_dir / f"daily_balance_{args.seller}_{args.month}.txt"
    report_path.write_text(report, encoding="utf-8")
    print(f"\nReport saved to: {report_path}")


if __name__ == "__main__":
    main()
