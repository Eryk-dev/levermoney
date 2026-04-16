"""ERR-0010 — refund_created amount must align with extrato debito_divida_disputa.

When a payment is refunded via dispute (status_detail=bpp_refunded),
MP debits the seller for an amount that includes interest/admin fees.
The system's refund_created event uses transaction_amount, which often
differs from MP's actual debit. The extrato is the ground truth for
cash flow.

Contract:
- When events have refund_created AND extrato has debito_divida_disputa
  for the same pid, the system refund_created movement amount must equal
  the extrato debito amount.
- The corresponding mp_expense reembolso_disputa already provides the
  credit-side via dedup, so per-pid net balances out exactly.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from app.services.reconciliation import (
    CashMovement,
    align_refund_created_with_extrato,
    events_to_payment_movements,
)


pytestmark = pytest.mark.architecture


def _evt(pid: int, etype: str, amount: float, event_date: str,
         release_date: str | None = None, status_detail: str | None = None) -> dict:
    meta: dict = {}
    if release_date:
        meta["money_release_date"] = release_date
    if status_detail:
        meta["status_detail"] = status_detail
    return {
        "ml_payment_id": pid,
        "event_type": etype,
        "signed_amount": amount,
        "event_date": event_date,
        "metadata": meta,
    }


def _ext_debito(pid: int, amount: float, date: str) -> CashMovement:
    return CashMovement(
        date=date,
        ref_id=str(pid),
        amount=Decimal(str(amount)),
        category="debito_divida_disputa",
        source="extrato",
        tx_type="Débito por dívida Reclamações no Mercado Livre",
    )


class TestDisputeExtratoAlignment:
    def test_extrato_debit_overrides_refund_created_amount(self):
        """Real case: 140915607218 — sys -111.60 must align to ext -119.37."""
        pid = 140915607218
        # Suppress refund_fee via dedup (mp_expense reembolso_generico exists)
        sys_movs = events_to_payment_movements(
            [
                _evt(pid, "sale_approved", 111.60, "2026-01-06", "2026-01-12",
                     status_detail="bpp_refunded"),
                _evt(pid, "fee_charged", -38.40, "2026-01-06"),
                _evt(pid, "refund_created", -111.60, "2026-01-12"),
                _evt(pid, "refund_fee", 38.40, "2026-01-12"),
            ],
            pids_with_fee_refund_expense={str(pid)},
        )
        ext_movs = [_ext_debito(pid, -119.37, "2026-01-12")]

        aligned = align_refund_created_with_extrato(sys_movs, ext_movs)

        refund_movs = [m for m in aligned
                       if m.ref_id == str(pid) and m.meta.get("group") == "refund_debit"]
        assert len(refund_movs) == 1
        assert refund_movs[0].amount == Decimal("-119.37"), (
            f"refund_created should align to extrato -119.37, got {refund_movs[0].amount}"
        )

    def test_pattern_b_sys_more_negative_than_extrato(self):
        """Real case: 140240998217 — sys -647.88 must align to ext -458.65."""
        pid = 140240998217
        sys_movs = events_to_payment_movements(
            [
                _evt(pid, "sale_approved", 647.88, "2026-01-06", "2026-01-20",
                     status_detail="bpp_refunded"),
                _evt(pid, "fee_charged", -61.74, "2026-01-06"),
                _evt(pid, "shipping_charged", -44.45, "2026-01-06"),
                _evt(pid, "refund_created", -647.88, "2026-01-21"),
                _evt(pid, "refund_fee", 61.74, "2026-01-21"),
                _evt(pid, "refund_shipping", 44.45, "2026-01-21"),
            ],
            pids_with_fee_refund_expense={str(pid)},
        )
        ext_movs = [_ext_debito(pid, -458.65, "2026-01-21")]

        aligned = align_refund_created_with_extrato(sys_movs, ext_movs)

        refund_movs = [m for m in aligned
                       if m.ref_id == str(pid) and m.meta.get("group") == "refund_debit"]
        assert len(refund_movs) == 1
        assert refund_movs[0].amount == Decimal("-458.65"), (
            f"refund_created should align to extrato -458.65, got {refund_movs[0].amount}"
        )

    def test_no_extrato_debit_means_suppress_refund_movements(self):
        """Real case: 140688038213 (by_admin) — no extrato → suppress refund movs."""
        pid = 140688038213
        sys_movs = events_to_payment_movements(
            [
                _evt(pid, "sale_approved", 355.94, "2026-01-09", "2026-01-24",
                     status_detail="by_admin"),
                _evt(pid, "fee_charged", -42.71, "2026-01-09"),
                _evt(pid, "refund_created", -355.94, "2026-01-24"),
                _evt(pid, "refund_fee", 42.71, "2026-01-24"),
            ],
        )
        ext_movs: list[CashMovement] = []  # no extrato lines for this pid

        aligned = align_refund_created_with_extrato(sys_movs, ext_movs)

        for_pid = [m for m in aligned if m.ref_id == str(pid)]
        assert for_pid == [], (
            "Refund movements must be suppressed when no extrato debito exists; "
            f"got {[(m.category, m.amount, m.meta.get('group')) for m in for_pid]}"
        )

    def test_no_refund_created_event_does_not_affect_other_pids(self):
        """Pids without refund_created should be untouched by alignment."""
        pid = 999999
        sys_movs = events_to_payment_movements(
            [
                _evt(pid, "sale_approved", 100.00, "2026-01-01", "2026-01-03"),
                _evt(pid, "fee_charged", -10.00, "2026-01-01"),
            ],
        )
        ext_movs: list[CashMovement] = []  # any extrato is irrelevant

        aligned = align_refund_created_with_extrato(sys_movs, ext_movs)

        for_pid = [m for m in aligned if m.ref_id == str(pid)]
        assert len(for_pid) == 1
        assert for_pid[0].meta.get("group") == "release"
        assert for_pid[0].amount == Decimal("90.00")

    def test_pid_without_extrato_dispute_keeps_refund_when_other_extrato_exists(self):
        """If extrato has only a release line for pid (no debito_divida_disputa),
        refund_created should still be suppressed (orphan would stay)."""
        pid = 143909170600
        sys_movs = events_to_payment_movements(
            [
                _evt(pid, "sale_approved", 45.90, "2026-01-28", "2026-01-29",
                     status_detail="refunded"),
                _evt(pid, "fee_charged", -14.30, "2026-01-28"),
                _evt(pid, "refund_created", -45.90, "2026-01-29"),
                _evt(pid, "refund_fee", 14.30, "2026-01-29"),
                _evt(pid, "refund_shipping", 46.99, "2026-01-29"),
            ],
        )
        # Extrato has nothing for this pid
        ext_movs: list[CashMovement] = []

        aligned = align_refund_created_with_extrato(sys_movs, ext_movs)

        for_pid = [m for m in aligned if m.ref_id == str(pid)]
        assert for_pid == [], (
            "Refunded payment with no extrato presence is a phantom; suppress all movs. "
            f"Got {[(m.category, m.amount, m.meta.get('group')) for m in for_pid]}"
        )
