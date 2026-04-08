"""Zero-sum ledger invariant tests.

Validates that the algebraic sum of events per payment lifecycle
matches the expected net balance. Pure unit tests — no DB needed.
"""

import pytest

from app.services.event_ledger import validate_event


def _validate_and_sum(events: list[tuple[str, float]]) -> float:
    """Validate each event and return the algebraic sum."""
    total = 0.0
    for event_type, signed_amount in events:
        validate_event(event_type, signed_amount)
        total += signed_amount
    return total


# -- Scenarios ----------------------------------------------------------

SCENARIOS = [
    pytest.param(
        [
            ("sale_approved", 1000.0),
            ("fee_charged", -150.0),
            ("shipping_charged", -50.0),
        ],
        800.0,
        id="approved-simple",
    ),
    pytest.param(
        [
            ("sale_approved", 1000.0),
            ("fee_charged", -150.0),
            ("shipping_charged", -50.0),
            ("refund_created", -1000.0),
            ("refund_fee", 150.0),
            ("refund_shipping", 50.0),
        ],
        0.0,
        id="full-refund-sums-to-zero",
    ),
    pytest.param(
        [
            ("sale_approved", 1000.0),
            ("fee_charged", -150.0),
            ("partial_refund", -300.0),
        ],
        550.0,
        id="partial-refund-by-amount",
    ),
    pytest.param(
        [
            ("sale_approved", 1000.0),
            ("fee_charged", -150.0),
            ("charged_back", -1000.0),
            ("reimbursed", 1000.0),
        ],
        850.0,
        id="chargeback-reimbursed",
    ),
    pytest.param(
        [
            ("sale_approved", 1000.0),
            ("fee_charged", -150.0),
            ("shipping_charged", -50.0),
            ("subsidy_credited", 20.0),
        ],
        820.0,
        id="approved-with-subsidy",
    ),
    pytest.param(
        [
            ("sale_approved", 1000.0),
            ("fee_charged", -150.0),
            ("shipping_charged", -50.0),
            ("partial_refund", -400.0),
            ("refund_fee", 150.0),
            ("refund_shipping", 50.0),
        ],
        600.0,
        id="partial-refund-with-fee-reversal",
    ),
    pytest.param(
        [
            ("sale_approved", 1000.0),
            ("fee_charged", -150.0),
            ("charged_back", -1000.0),
        ],
        -150.0,
        id="chargeback-not-reimbursed",
    ),
]


class TestLedgerInvariants:
    """Payment lifecycle events must produce expected net balance."""

    @pytest.mark.parametrize("events, expected_balance", SCENARIOS)
    def test_sum_equals_expected(self, events, expected_balance):
        total = _validate_and_sum(events)
        assert abs(total - expected_balance) < 0.01, (
            f"Expected {expected_balance}, got {total}"
        )

    @pytest.mark.parametrize("events, expected_balance", SCENARIOS)
    def test_all_events_pass_validation(self, events, expected_balance):
        """Each event individually passes validate_event (no ValueError)."""
        for event_type, signed_amount in events:
            validate_event(event_type, signed_amount)
