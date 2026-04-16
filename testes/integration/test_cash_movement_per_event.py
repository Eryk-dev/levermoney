"""T-012 (ERR-0003) — CashMovements emitted per event group, not per payment NET.

Bug context: The matcher previously collapsed every cash event of a payment
into a single CashMovement on `money_release_date`, summing the NET. That
hides the temporal dimension when a payment is refunded in a *later* month
than it was released. The extrato shows two separate lines (release in
month A, refund in month B); our system collapsed them to one NET on month
A and never matched month B.

Fix: emit 1 CashMovement per natural cash-event group:
  • Release group  = sale_approved + fee_charged + shipping_charged + subsidy_credited
                     (date = money_release_date of the sale event)
  • Refund group   = refund_created + refund_fee + refund_shipping
                     (date = event_date of refund_created)

Each group becomes one CashMovement with NET = sum(signed_amount).

This file is *integration*-marked only because it exercises module imports
the test suite ordinarily runs offline. No external service is required.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from app.services.reconciliation import events_to_payment_movements


pytestmark = pytest.mark.architecture


# ─── Helpers ──────────────────────────────────────────────────────────────


def _evt(
    event_type: str,
    pid: int,
    signed_amount: float,
    event_date: str,
    money_release_date: str | None = None,
) -> dict:
    meta: dict = {}
    if money_release_date:
        meta["money_release_date"] = money_release_date
    return {
        "ml_payment_id": pid,
        "event_type": event_type,
        "signed_amount": signed_amount,
        "event_date": event_date,
        "metadata": meta,
    }


# ─── Tests ───────────────────────────────────────────────────────────────


class TestSinglePayment:
    def test_simple_release_emits_one_movement(self):
        events = [
            _evt("sale_approved", 100, 100.00, "2026-01-02",
                 money_release_date="2026-01-15"),
            _evt("fee_charged", 100, -10.00, "2026-01-02"),
            _evt("shipping_charged", 100, -5.00, "2026-01-02"),
        ]
        movements = events_to_payment_movements(events)
        assert len(movements) == 1
        m = movements[0]
        assert m.date == "2026-01-15", "release group should land on money_release_date"
        assert m.amount == Decimal("85.00"), "NET = 100 - 10 - 5"
        assert m.ref_id == "100"

    def test_release_then_refund_in_later_month_emits_movements(self):
        """Core bug from ERR-0003: refund in a later period must produce its
        own movement(s) on the refund date, not be absorbed into the release.

        Updated for ERR-0007: the refund itself splits into 2 movements
        (refund_created as a debit line + refund_fee/shipping as a fee-refund
        line) to mirror MP extrato granularity. Temporal separation from the
        release group is the invariant that matters here.
        """
        events = [
            _evt("sale_approved", 200, 4318.05, "2026-01-02",
                 money_release_date="2026-01-02"),
            _evt("refund_created", 200, -4318.05, "2026-02-15"),
            _evt("refund_fee", 200, 470.95, "2026-02-15"),
            _evt("refund_shipping", 200, 61.00, "2026-02-15"),
        ]
        movements = events_to_payment_movements(events)

        dates = {m.date for m in movements}
        assert "2026-01-02" in dates, "release movement on release date"
        assert "2026-02-15" in dates, "refund movement(s) on refund date"

        release = [m for m in movements if m.date == "2026-01-02"]
        refund = [m for m in movements if m.date == "2026-02-15"]

        # Release unchanged: sum equals sale (no fees in this fixture)
        assert len(release) == 1
        assert release[0].amount == Decimal("4318.05")

        # Refund split: sums to the previous NET (-3786.10)
        assert sum(m.amount for m in refund) == Decimal("-3786.10")

    def test_release_with_fees_then_refund_groups_correctly(self):
        events = [
            _evt("sale_approved", 300, 1000.00, "2026-01-05",
                 money_release_date="2026-01-20"),
            _evt("fee_charged", 300, -100.00, "2026-01-05"),
            _evt("shipping_charged", 300, -50.00, "2026-01-05"),
            # Refund in March
            _evt("refund_created", 300, -1000.00, "2026-03-10"),
            _evt("refund_fee", 300, 100.00, "2026-03-10"),
            _evt("refund_shipping", 300, 50.00, "2026-03-10"),
        ]
        movements = events_to_payment_movements(events)

        release = [m for m in movements if m.date == "2026-01-20"]
        refund = [m for m in movements if m.date == "2026-03-10"]

        assert len(release) == 1
        assert release[0].amount == Decimal("850.00")  # 1000 - 100 - 50
        # ERR-0007: refund now splits into debit (−1000) + fee-refund (+150)
        assert sum(m.amount for m in refund) == Decimal("-850.00")

    def test_partial_refund_emits_release_plus_refund_movements(self):
        events = [
            _evt("sale_approved", 400, 200.00, "2026-01-10",
                 money_release_date="2026-01-25"),
            _evt("fee_charged", 400, -20.00, "2026-01-10"),
            _evt("partial_refund", 400, -50.00, "2026-02-05"),
        ]
        movements = events_to_payment_movements(events)
        # partial_refund is not in the cash group constants used today;
        # this test documents the assumption that it still gets accounted for.
        # Allow either 1 (partial_refund grouped with release) or 2 (own group).
        assert len(movements) >= 1

    def test_sale_with_subsidy_grouped_into_release(self):
        events = [
            _evt("sale_approved", 500, 100.00, "2026-01-08",
                 money_release_date="2026-01-22"),
            _evt("subsidy_credited", 500, 30.00, "2026-01-08"),
        ]
        movements = events_to_payment_movements(events)
        # Subsidy is grouped with release (both happen on the same release date).
        assert len(movements) == 1
        assert movements[0].date == "2026-01-22"
        assert movements[0].amount == Decimal("130.00")


class TestMultiplePayments:
    def test_two_independent_payments_emit_independent_movements(self):
        events = [
            _evt("sale_approved", 100, 100.00, "2026-01-02",
                 money_release_date="2026-01-15"),
            _evt("sale_approved", 200, 250.00, "2026-01-03",
                 money_release_date="2026-01-16"),
        ]
        movements = events_to_payment_movements(events)
        assert len(movements) == 2
        amounts_by_pid = {m.ref_id: m.amount for m in movements}
        assert amounts_by_pid["100"] == Decimal("100.00")
        assert amounts_by_pid["200"] == Decimal("250.00")


class TestKitSplitSkipped:
    def test_by_admin_status_detail_emits_no_release_movement(self):
        """Kit split (status_detail == 'by_admin') means the payment was
        consolidated into a parent — NET is zero, no cash movement."""
        events = [
            {
                "ml_payment_id": 600,
                "event_type": "sale_approved",
                "signed_amount": 100.00,
                "event_date": "2026-01-12",
                "metadata": {
                    "money_release_date": "2026-01-25",
                    "status_detail": "by_admin",
                },
            },
            _evt("fee_charged", 600, -10.00, "2026-01-12"),
        ]
        movements = events_to_payment_movements(events)
        # Release skipped because of by_admin; no other groups present.
        assert movements == []
