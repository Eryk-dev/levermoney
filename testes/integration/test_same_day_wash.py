"""ERR-0008 — same-day release+refund wash must emit no movements.

When a payment is released AND refunded on the same day (kit split,
immediate cancellation), MP's extrato shows no line — the transactions
net out internally. Our reconciliation previously emitted three orphan
movements (release +net, refund_debit -sale, refund_fee +fees) summing
to zero, which cluttered the orphan_system counter.

Contract: suppress all three movements when release + refund_debit +
refund_fee of the same payment on the same date sum to within tolerance
of zero.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from app.services.reconciliation import events_to_payment_movements


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


class TestSameDayWash:
    def test_same_day_release_and_refund_emits_nothing(self):
        """status_detail='refunded' + same-day: MP nets invisibly."""
        pid = 143762867120
        events = [
            _evt(pid, "sale_approved", 5000.00, "2026-01-28", "2026-01-28",
                 status_detail="refunded"),
            _evt(pid, "fee_charged", -850.00, "2026-01-28"),
            _evt(pid, "shipping_charged", -28.45, "2026-01-28"),
            _evt(pid, "refund_created", -5000.00, "2026-01-28"),
            _evt(pid, "refund_fee", 850.00, "2026-01-28"),
            _evt(pid, "refund_shipping", 28.45, "2026-01-28"),
        ]
        movements = events_to_payment_movements(events)
        for_pid = [m for m in movements if m.ref_id == str(pid)]
        assert for_pid == [], (
            "Same-day release+refund (status=refunded) summing to zero must emit no movements; "
            f"got {[(m.category, m.amount) for m in for_pid]}"
        )

    def test_bpp_refunded_same_day_still_emits_three_movements(self):
        """status_detail='bpp_refunded' hits the extrato as 3 lines; do NOT wash."""
        pid = 139749344683
        events = [
            _evt(pid, "sale_approved", 4850.00, "2026-01-02", "2026-01-02",
                 status_detail="bpp_refunded"),
            _evt(pid, "fee_charged", -507.00, "2026-01-02"),
            _evt(pid, "shipping_charged", -24.95, "2026-01-02"),
            _evt(pid, "refund_created", -4850.00, "2026-01-02"),
            _evt(pid, "refund_fee", 507.00, "2026-01-02"),
            _evt(pid, "refund_shipping", 24.95, "2026-01-02"),
        ]
        movements = events_to_payment_movements(events)
        for_pid = [m for m in movements if m.ref_id == str(pid)]
        assert len(for_pid) == 3, (
            "bpp_refunded must emit release+debit+fee-refund even when same-day; "
            f"got {[(m.category, m.amount) for m in for_pid]}"
        )

    def test_different_day_release_refund_still_emits_both(self):
        """Regression: cross-day refund must still emit its group."""
        pid = 200000000100
        events = [
            _evt(pid, "sale_approved", 100.0, "2026-01-05", "2026-01-05"),
            _evt(pid, "refund_created", -100.0, "2026-02-10"),
        ]
        movements = events_to_payment_movements(events)
        for_pid = [m for m in movements if m.ref_id == str(pid)]
        dates = {m.date for m in for_pid}
        assert "2026-01-05" in dates, "release still emitted when refund is in later period"
        assert "2026-02-10" in dates, "refund still emitted when release is earlier"

    def test_partial_same_day_refund_still_emits(self):
        """If same-day refund doesn't null out the release, keep all movements."""
        pid = 200000000101
        events = [
            _evt(pid, "sale_approved", 100.0, "2026-01-05", "2026-01-05"),
            _evt(pid, "fee_charged", -10.0, "2026-01-05"),
            _evt(pid, "refund_created", -50.0, "2026-01-05"),   # partial refund
        ]
        movements = events_to_payment_movements(events)
        for_pid = [m for m in movements if m.ref_id == str(pid)]
        # Release +90, refund_debit -50 → nonzero NET, keep both
        total = sum(m.amount for m in for_pid)
        assert total == Decimal("40.00"), f"partial refund NET should be +40, got {total}"
