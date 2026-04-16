"""ERR-0007 — refund group must split into 2 movements (debit + fee refund).

MP extrato records a dispute refund as TWO distinct lines:
  • "Débito por dívida Reclamações no Mercado Livre"  -sale_amount
  • "Reembolso Envío cancelado ..."                   +fees_value

Our previous implementation collapsed the whole refund group into one
CashMovement with the NET (-sale + fees = -net), which caused:
  • amount_diff against the extrato debit line (off by fees)
  • extrato fee-refund line going orphan

This test locks the new contract: 1 refund group → 2 movements.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from app.services.reconciliation import events_to_payment_movements


pytestmark = pytest.mark.architecture


def _evt(
    pid: int, etype: str, amount: float, event_date: str,
    release_date: str | None = None,
) -> dict:
    return {
        "ml_payment_id": pid,
        "event_type": etype,
        "signed_amount": amount,
        "event_date": event_date,
        "metadata": {"money_release_date": release_date} if release_date else {},
    }


class TestRefundSplit:
    """Refund group emits 2 movements, one per MP extrato line."""

    def test_full_refund_emits_debit_and_fee_refund(self):
        """Release and refund on different dates → 3 movements (no wash).

        Same-day wash is covered by ERR-0008 (test_same_day_wash); here we
        force cross-day so the split is exercised end-to-end.
        """
        pid = 139749344683
        events = [
            _evt(pid, "sale_approved", 4850.0, "2026-01-02", "2026-01-02"),
            _evt(pid, "fee_charged", -507.0, "2026-01-02"),
            _evt(pid, "shipping_charged", -24.95, "2026-01-02"),
            _evt(pid, "refund_created", -4850.0, "2026-02-15"),
            _evt(pid, "refund_fee", 507.0, "2026-02-15"),
            _evt(pid, "refund_shipping", 24.95, "2026-02-15"),
        ]
        movs = events_to_payment_movements(events)

        # 1 release + 2 refund = 3 total for this payment
        for_pid = [m for m in movs if m.ref_id == str(pid)]
        assert len(for_pid) == 3, (
            f"Expected 3 movements (release + debit + fee refund), got {len(for_pid)}: "
            f"{[(m.amount, m.category) for m in for_pid]}"
        )

        by_category = {m.category: m for m in for_pid}

        assert "liberacao" in by_category
        assert by_category["liberacao"].amount == Decimal("4318.05")

        assert "debito_divida_disputa" in by_category
        assert by_category["debito_divida_disputa"].amount == Decimal("-4850.00"), (
            "Debit movement must carry full refund_created amount, not the NET"
        )

        assert "reembolso_disputa" in by_category
        assert by_category["reembolso_disputa"].amount == Decimal("531.95"), (
            "Fee-refund movement must sum refund_fee + refund_shipping (positive)"
        )

    def test_refund_without_fees_emits_only_debit(self):
        """Partial refunds with no fee return: only the debit movement."""
        pid = 100000000001
        events = [
            _evt(pid, "sale_approved", 100.0, "2026-01-05", "2026-01-05"),
            _evt(pid, "fee_charged", -10.0, "2026-01-05"),
            _evt(pid, "refund_created", -100.0, "2026-01-06"),
        ]
        movs = events_to_payment_movements(events)
        refund_movs = [m for m in movs if m.ref_id == str(pid) and m.amount < 0 and m.category == "debito_divida_disputa"]
        assert len(refund_movs) == 1
        assert refund_movs[0].amount == Decimal("-100.00")

        # No fee-refund movement emitted when no refund_fee/shipping
        fee_refund = [m for m in movs if m.ref_id == str(pid) and m.category == "reembolso_disputa"]
        assert fee_refund == []

    def test_refund_fee_without_created_emits_only_fee_refund(self):
        """Defensive: if only refund_fee is present, emit only the fee-refund movement."""
        pid = 100000000002
        events = [
            _evt(pid, "sale_approved", 50.0, "2026-01-05", "2026-01-05"),
            _evt(pid, "fee_charged", -5.0, "2026-01-05"),
            _evt(pid, "refund_fee", 5.0, "2026-01-06"),
        ]
        movs = events_to_payment_movements(events)
        # No refund_created → debit movement must not be emitted
        debit = [m for m in movs if m.ref_id == str(pid) and m.category == "debito_divida_disputa"]
        assert debit == []
        # refund_fee alone still becomes a fee-refund movement
        fee_refund = [m for m in movs if m.ref_id == str(pid) and m.category == "reembolso_disputa"]
        assert len(fee_refund) == 1
        assert fee_refund[0].amount == Decimal("5.00")

    def test_release_group_net_unchanged_by_refund_split(self):
        """Regression: release group NET must still be sale - fee - shipping."""
        pid = 200000000001
        events = [
            _evt(pid, "sale_approved", 100.0, "2026-01-05", "2026-01-05"),
            _evt(pid, "fee_charged", -10.0, "2026-01-05"),
            _evt(pid, "shipping_charged", -5.0, "2026-01-05"),
        ]
        movs = events_to_payment_movements(events)
        release = [m for m in movs if m.ref_id == str(pid)]
        assert len(release) == 1
        assert release[0].amount == Decimal("85.00")
        assert release[0].category == "liberacao"
