#!/usr/bin/env python3
"""Debug: dump orphan match details for a reconciliation run.

Usage:
    python3 scripts/debug_orphans.py <seller> <period>
    python3 scripts/debug_orphans.py 141air 2026-02
"""
from __future__ import annotations

import calendar
import json
import sys
from decimal import Decimal
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.db.supabase import get_db
from app.services.reconciliation import (
    _FEE_REFUND_DEDUP_EXPENSE_TYPES,
    align_refund_created_with_extrato,
    events_to_payment_movements,
    expenses_to_movements,
    extrato_to_movements,
    filter_stale_mp_expenses,
    load_contract,
    load_events_for_pids,
    load_extrato,
    load_mp_expenses,
    load_mp_expenses_for_pids,
    load_payment_events,
    match_movements,
)


def main() -> int:
    seller, period = sys.argv[1], sys.argv[2]
    contract = load_contract()
    tolerance = Decimal(str(contract["tolerances"]["per_line_brl"]))

    period_start = f"{period}-01"
    year, month = int(period[:4]), int(period[5:7])
    last_day = calendar.monthrange(year, month)[1]
    period_end = f"{period}-{last_day:02d}"

    extrato_summary, transactions = load_extrato(seller, period)

    db = get_db()
    events = load_payment_events(db, seller, period_start, period_end)
    extrato_pids = {int(tx["reference_id"]) for tx in transactions
                    if str(tx["reference_id"]).isdigit()}
    current_pids = {int(e["ml_payment_id"]) for e in events if e.get("ml_payment_id")}
    extra_pids = list(extrato_pids - current_pids)
    if extra_pids:
        events.extend(load_events_for_pids(db, seller, extra_pids))

    expenses = load_mp_expenses(db, seller, period_start, period_end)
    in_period_pids = {
        str(ex.get("payment_id") or "").split(":")[0]
        for ex in expenses if ex.get("payment_id")
    }
    extra_expense_pids = [p for p in extrato_pids if str(p) not in in_period_pids]
    if extra_expense_pids:
        for ex in load_mp_expenses_for_pids(db, seller, extra_expense_pids):
            expenses.append(ex)

    payment_ids = {int(e["ml_payment_id"]) for e in events if e.get("ml_payment_id")}
    approved_pids = {
        int(e["ml_payment_id"]) for e in events
        if e.get("event_type") == "sale_approved" and e.get("ml_payment_id")
    }
    extrato_pids_str = {str(p) for p in extrato_pids}
    expenses = filter_stale_mp_expenses(expenses, approved_pids, extrato_pids_str)

    pids_with_fee_refund_expense = {
        str(ex.get("payment_id") or "").split(":")[0]
        for ex in expenses
        if ex.get("expense_type") in _FEE_REFUND_DEDUP_EXPENSE_TYPES
        and str(ex.get("payment_id") or "").split(":")[0].isdigit()
    }

    ext_movs = extrato_to_movements(transactions, payment_ids)

    extrato_date_overrides: dict[str, str] = {}
    if extra_expense_pids:
        extra_pid_strs = {str(p) for p in extra_expense_pids}
        for ext in ext_movs:
            if ext.ref_id in extra_pid_strs:
                extrato_date_overrides[ext.ref_id] = ext.date

    sys_movs = (
        events_to_payment_movements(events, pids_with_fee_refund_expense)
        + expenses_to_movements(expenses, extrato_date_overrides)
    )
    sys_movs = [m for m in sys_movs if period_start <= m.date <= period_end]
    sys_movs = align_refund_created_with_extrato(sys_movs, ext_movs)

    results = match_movements(ext_movs, sys_movs, tolerance)

    print("=== ORPHAN EXTRATO ===")
    for r in results:
        if r.status == "orphan_extrato":
            ext = r.extrato
            print(f"  {ext.date} cat={ext.category:<28} ref={ext.ref_id:<20} "
                  f"amt={float(ext.amount):>12,.2f}  tx_type={ext.tx_type[:60]!r}")
            # Hunt for candidate sys movs with same ref
            cands = [m for m in sys_movs if m.ref_id == ext.ref_id]
            for c in cands:
                print(f"      -> sys ref={c.ref_id} cat={c.category} amt={float(c.amount):.2f} date={c.date} src={c.source} meta={c.meta}")

    print()
    print("=== ORPHAN SISTEMA ===")
    for r in results:
        if r.status == "orphan_system":
            s = r.system
            print(f"  {s.date} cat={s.category:<28} ref={s.ref_id:<20} "
                  f"amt={float(s.amount):>12,.2f}  source={s.source} meta={s.meta}")

    print()
    print("=== AMOUNT DIFFS ===")
    for r in results:
        if r.status == "amount_diff":
            e, s = r.extrato, r.system
            print(f"  {e.date} cat={e.category:<28} ref={e.ref_id:<20} "
                  f"ext={float(e.amount):>12,.2f} sys={float(s.amount):>12,.2f} "
                  f"diff={float(r.diff):>+10,.2f}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
