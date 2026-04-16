#!/usr/bin/env python3
"""Dump orphan/amount_diff breakdown for a given seller/period.

Usage: scripts/show_orphans.py 141air 2026-01 [--category liberacao]

Inspection tool used during reconciliation triage — not part of the gate.
"""
from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.services.reconciliation import (
    compute_metrics,
    events_to_payment_movements,
    expenses_to_movements,
    extrato_to_movements,
    filter_stale_mp_expenses,
    load_contract,
    load_events_for_pids,
    load_extrato,
    load_mp_expenses,
    load_payment_events,
    match_movements,
)
from app.db.supabase import get_db


def main(seller: str, period: str, cat_filter: str | None) -> int:
    contract = load_contract()
    tol = Decimal(str(contract["tolerances"]["per_line_brl"]))

    import calendar
    y, m = int(period[:4]), int(period[5:7])
    last = calendar.monthrange(y, m)[1]
    start = f"{period}-01"
    end = f"{period}-{last:02d}"

    summary, txs = load_extrato(seller, period)
    db = get_db()
    events = load_payment_events(db, seller, start, end)
    extrato_pids = {int(tx["reference_id"]) for tx in txs if str(tx["reference_id"]).isdigit()}
    current = {int(e["ml_payment_id"]) for e in events if e.get("ml_payment_id")}
    extra = list(extrato_pids - current)
    if extra:
        events.extend(load_events_for_pids(db, seller, extra))

    expenses = load_mp_expenses(db, seller, start, end)
    pids = {int(e["ml_payment_id"]) for e in events if e.get("ml_payment_id")}
    approved = {int(e["ml_payment_id"]) for e in events if e.get("event_type") == "sale_approved" and e.get("ml_payment_id")}
    expenses = filter_stale_mp_expenses(expenses, approved)

    ext_movs = extrato_to_movements(txs, pids)
    sys_movs = events_to_payment_movements(events) + expenses_to_movements(expenses)
    sys_movs = [mv for mv in sys_movs if start <= mv.date <= end]

    results = match_movements(ext_movs, sys_movs, tol)

    for r in results:
        if cat_filter:
            cat = (r.extrato.category if r.extrato else (r.system.category if r.system else ""))
            if cat != cat_filter:
                continue
        if r.status in ("orphan_extrato", "orphan_system", "amount_diff"):
            ext_s = f"{r.extrato.date} {r.extrato.ref_id} {r.extrato.amount} {r.extrato.category} [{r.extrato.tx_type}]" if r.extrato else "---"
            sys_s = f"{r.system.date} {r.system.ref_id} {r.system.amount} {r.system.category} src={r.system.source}" if r.system else "---"
            print(f"{r.status:16} | EXT: {ext_s} | SYS: {sys_s}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: show_orphans.py <seller> <period> [--category X]")
        sys.exit(2)
    cat = None
    if "--category" in sys.argv:
        i = sys.argv.index("--category")
        cat = sys.argv[i + 1]
    sys.exit(main(sys.argv[1], sys.argv[2], cat))
