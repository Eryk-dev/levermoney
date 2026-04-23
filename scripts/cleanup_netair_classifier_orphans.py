#!/usr/bin/env python3
"""Clean up net-air Jan 2026 classifier orphans.

Two families of orphan sys rows remain after ERR-0021..0026 fixes:

A) Classifier row has a same-base-pid extrato-ingester companion row (where
   the ingester row has the correct category matching the extrato line).
   The classifier row is a semantic duplicate → delete.

B) Classifier row has no extrato counterpart at all (phantom MP-internal
   events like failed ML collection attempts, recurring card charges that
   settle outside the MP account_statement). No cash event in extrato →
   drop from reconciliation scope.

Scope: net-air, Jan 2026. Safe and reversible (can be re-ingested by
sync_seller_payments if needed).
"""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.db.supabase import get_db


# pids to drop. Each is a known classifier-origin orphan for net-air Jan 2026.
#
# Family A — duplicated by extrato-ingester suffix row:
#   142672762774 (transfer_intra vs :to transferencia_pix_out)
#   142378285359 (deposit vs :ti transferencia_pix_in)
#   140353250703 (other vs :ln liberacao_nao_sync; amounts differ slightly)
#
# Family B — phantom / not in extrato:
#   141095472011 (Paramount+ recurring card charge; not in MP extrato)
#   141161237273 (MELIPAYMENTS-COLLECTIONATTEMPT; failed ML collection)
#   142984661088 (NetAir Ar Condicionado; unknown MP-internal event)
ORPHAN_PIDS = [
    142672762774,
    142378285359,
    140353250703,
    141095472011,
    141161237273,
    142984661088,
]


def main() -> int:
    seller = "net-air"
    db = get_db()

    deleted = 0
    for pid in ORPHAN_PIDS:
        res = (
            db.table("payment_events")
            .delete()
            .eq("seller_slug", seller)
            .eq("ml_payment_id", pid)
            .eq("reference_id", str(pid))  # only non-suffixed row
            .in_("event_type", ["expense_captured", "expense_classified"])
            .execute()
        )
        n = len(res.data or [])
        deleted += n
        print(f"  pid={pid}: deleted {n} rows")

    print(f"Deleted {deleted} classifier-origin rows")
    return 0


if __name__ == "__main__":
    sys.exit(main())
