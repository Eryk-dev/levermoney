"""
Ingest extrato lines as cash events in the payment_events ledger.

Every line from the extrato CSV becomes a cash_* event. This enables
reconciliation: sum(cash events) == final_balance - initial_balance.

Usage:
    python3 testes/ingest_extrato_to_ledger.py --seller 141air --month jan2026 [--dry-run]

This script WRITES to payment_events in Supabase (unless --dry-run).
"""
import argparse
import asyncio
import sys
from collections import Counter
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.services.extrato_ingester import (
    _parse_account_statement,
    _classify_extrato_line,
    _resolve_check_payments,
    _normalize_text,
    _CHECK_PAYMENTS,
    _EXPENSE_TYPE_ABBREV,
    _batch_lookup_payment_ids,
)
from app.services.event_ledger import (
    record_cash_event,
    EVENT_TYPES,
    CASH_TYPE_MAP,
    SKIP_TO_CASH_TYPE,
    SKIP_ABBREV,
)
from app.db.supabase import get_db


EXTRATO_FILES = {
    ("141air", "jan2026"): "testes/data/extratos/extrato janeiro 141Air.csv",
    ("141air", "fev2026"): "testes/data/extratos/extrato fevereiro 141Air.csv",
}


def _align_cash_type_to_sign(cash_type: str, amount: float) -> str:
    """Correct cash event type when amount sign conflicts with expected direction.

    Some extrato lines (e.g. "Pagamento com QR Pix RECEITA FEDERAL") are
    classified as income but have negative amounts (outgoing payments).
    The actual cash direction (sign) takes precedence.
    """
    expected = EVENT_TYPES[cash_type]
    if expected == "positive" and amount < 0:
        return "cash_expense"
    if expected == "negative" and amount > 0:
        return "cash_income"
    return cash_type


def _match_skip_rule(normalized_tx_type: str) -> tuple[str | None, str | None]:
    """Match normalized transaction type against SKIP_TO_CASH_TYPE rules.

    Returns (cash_event_type, abbreviation) or (None, None) if no match.
    Uses substring matching in dict insertion order (more specific keys first).
    """
    for pattern, cash_type in SKIP_TO_CASH_TYPE.items():
        if pattern in normalized_tx_type:
            abbrev = SKIP_ABBREV.get(pattern, "xx")
            return cash_type, abbrev
    return None, None


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
    expected_net = round(summary["final_balance"] - summary["initial_balance"], 2)
    print(f"Parsed extrato: {len(transactions)} lines")
    print(f"  initial={summary['initial_balance']}, final={summary['final_balance']}")
    print(f"  Expected net (final - initial): {expected_net}")

    # 2. Batch lookup payment_events for _CHECK_PAYMENTS resolution
    db = get_db()
    all_ref_ids = list({tx["reference_id"] for tx in transactions})
    payment_ids_in_db = await _batch_lookup_payment_ids(db, seller_slug, all_ref_ids)
    print(f"  payment_events: {len(payment_ids_in_db)} ref_ids found")

    # 3. Map each line → cash event type + abbreviation
    events_to_record: list[dict] = []
    type_counts: Counter = Counter()
    seen_keys: Counter = Counter()  # Track key occurrences for dedup suffix

    for tx in transactions:
        ref_id = tx["reference_id"]
        normalized = _normalize_text(tx["transaction_type"])
        amount = tx["amount"]
        event_date = tx["date"]

        expense_type, direction, _ca_cat = _classify_extrato_line(tx["transaction_type"])

        if expense_type is None and direction is None:
            # Unconditional skip in mp_expenses → map via SKIP_TO_CASH_TYPE
            cash_type, abbrev = _match_skip_rule(normalized)
            if cash_type is None:
                print(f"  WARNING: No skip rule for: {tx['transaction_type']!r}")
                continue
            extrato_type = "skip"

        elif expense_type == _CHECK_PAYMENTS:
            if ref_id in payment_ids_in_db:
                # Payment exists in ledger → cash_release
                cash_type = "cash_release"
                abbrev = "cr"
                extrato_type = "check_payments_found"
            else:
                # Payment not in DB → resolve fallback → CASH_TYPE_MAP
                fallback_type, _fallback_dir = _resolve_check_payments(tx["transaction_type"])
                cash_type = CASH_TYPE_MAP.get(fallback_type)
                if cash_type is None:
                    print(f"  WARNING: No CASH_TYPE_MAP for fallback {fallback_type!r}: {tx['transaction_type']!r}")
                    continue
                abbrev = _EXPENSE_TYPE_ABBREV.get(fallback_type, "xx")
                extrato_type = fallback_type

        else:
            # Classified line → CASH_TYPE_MAP
            cash_type = CASH_TYPE_MAP.get(expense_type)
            if cash_type is None:
                print(f"  WARNING: No CASH_TYPE_MAP for {expense_type!r}: {tx['transaction_type']!r}")
                continue
            abbrev = _EXPENSE_TYPE_ABBREV.get(expense_type, "xx")
            extrato_type = expense_type

        # Align cash type to actual amount sign (extrato amount is truth)
        cash_type = _align_cash_type_to_sign(cash_type, amount)

        # Deduplicate: same ref_id + cash_type + date + abbrev can appear
        # twice in dispute scenarios (e.g. two reembolso_disputa lines).
        # Append sequence number to abbreviation to make keys unique.
        base_key = f"{seller_slug}:{ref_id}:{cash_type}:{event_date}:{abbrev}"
        seen_keys[base_key] += 1
        if seen_keys[base_key] > 1:
            abbrev = f"{abbrev}{seen_keys[base_key]}"

        events_to_record.append({
            "seller_slug": seller_slug,
            "reference_id": ref_id,
            "event_type": cash_type,
            "signed_amount": amount,
            "event_date": event_date,
            "extrato_type": extrato_type,
            "expense_type_abbrev": abbrev,
        })
        type_counts[cash_type] += 1

    # Print summary
    print(f"\nTotal lines: {len(transactions)}")
    print(f"Mapped to cash events: {len(events_to_record)}")
    print(f"\nCount per cash event type:")
    for ct in sorted(type_counts.keys()):
        print(f"  {type_counts[ct]:4d}  {ct}")

    actual_sum = round(sum(e["signed_amount"] for e in events_to_record), 2)
    print(f"\nSUM of cash events: {actual_sum}")
    print(f"Expected net:       {expected_net}")
    match = abs(actual_sum - expected_net) < 0.01
    print(f"Reconciliation:     {'PASS' if match else 'FAIL'}")

    if dry_run:
        print("\n=== DRY RUN — no events recorded ===")
        return

    if not match:
        print("\nERROR: Reconciliation failed. Not recording events.")
        sys.exit(1)

    # 4. Record events
    newly_ingested = 0
    already_exist = 0

    for evt in events_to_record:
        result = await record_cash_event(**evt)
        if result is not None:
            newly_ingested += 1
        else:
            already_exist += 1

    print(f"\n{'='*60}")
    print(f"RESULTS")
    print(f"  Total lines:      {len(transactions)}")
    print(f"  Cash events:      {len(events_to_record)}")
    print(f"  Newly ingested:   {newly_ingested}")
    print(f"  Already exist:    {already_exist}")
    print(f"  Sum:              {actual_sum}")


def main():
    parser = argparse.ArgumentParser(description="Ingest extrato lines as cash events")
    parser.add_argument("--seller", default="141air", help="Seller slug")
    parser.add_argument("--month", default="jan2026", help="Month key")
    parser.add_argument("--dry-run", action="store_true", help="Count and validate without writing")
    args = parser.parse_args()

    asyncio.run(run(args.seller, args.month, args.dry_run))


if __name__ == "__main__":
    main()
