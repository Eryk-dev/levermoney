"""
Unit tests for event_ledger.py — the append-only financial event log.

Tests pure functions only (no DB). Validates idempotency key format,
event type validation, and signed amount conventions.

Run: python3 -m pytest testes/test_event_ledger.py -v
"""
import pytest

from app.services.event_ledger import (
    build_idempotency_key,
    validate_event,
    derive_payment_status,
    EventRecordError,
    EVENT_TYPES,
)


# ===========================================================================
# build_idempotency_key
# ===========================================================================

class TestBuildIdempotencyKey:
    def test_basic_format(self):
        key = build_idempotency_key("141air", 12345, "sale_approved")
        assert key == "141air:12345:sale_approved"

    def test_with_suffix(self):
        key = build_idempotency_key("141air", 12345, "partial_refund", suffix="0")
        assert key == "141air:12345:partial_refund:0"

    def test_empty_suffix_no_trailing_colon(self):
        key = build_idempotency_key("netair", 99999, "fee_charged", suffix="")
        assert key == "netair:99999:fee_charged"
        assert not key.endswith(":")

    def test_different_sellers_different_keys(self):
        k1 = build_idempotency_key("141air", 100, "sale_approved")
        k2 = build_idempotency_key("netair", 100, "sale_approved")
        assert k1 != k2

    def test_different_payments_different_keys(self):
        k1 = build_idempotency_key("141air", 100, "sale_approved")
        k2 = build_idempotency_key("141air", 200, "sale_approved")
        assert k1 != k2

    def test_different_types_different_keys(self):
        k1 = build_idempotency_key("141air", 100, "sale_approved")
        k2 = build_idempotency_key("141air", 100, "fee_charged")
        assert k1 != k2

    def test_subsidy_key(self):
        key = build_idempotency_key("141air", 12345, "subsidy_credited")
        assert key == "141air:12345:subsidy_credited"

    def test_partial_refund_indexed(self):
        """Each partial refund gets a unique suffix (index)."""
        k0 = build_idempotency_key("141air", 100, "partial_refund", suffix="0")
        k1 = build_idempotency_key("141air", 100, "partial_refund", suffix="1")
        assert k0 != k1
        assert k0 == "141air:100:partial_refund:0"
        assert k1 == "141air:100:partial_refund:1"


# ===========================================================================
# validate_event
# ===========================================================================

class TestValidateEvent:
    """Validate that event types enforce correct sign conventions."""

    # --- Positive events (money IN) ---

    def test_sale_approved_positive(self):
        validate_event("sale_approved", 1500.00)

    def test_sale_approved_rejects_negative(self):
        with pytest.raises(ValueError, match="positive"):
            validate_event("sale_approved", -100.00)

    def test_subsidy_credited_positive(self):
        validate_event("subsidy_credited", 5.50)

    def test_refund_fee_positive(self):
        """Estorno de taxa devolve dinheiro ao seller."""
        validate_event("refund_fee", 120.00)

    def test_refund_shipping_positive(self):
        validate_event("refund_shipping", 45.30)

    def test_reimbursed_positive(self):
        """ML covered the chargeback — money back to seller."""
        validate_event("reimbursed", 500.00)

    # --- Negative events (money OUT) ---

    def test_fee_charged_negative(self):
        validate_event("fee_charged", -120.50)

    def test_fee_charged_rejects_positive(self):
        with pytest.raises(ValueError, match="negative"):
            validate_event("fee_charged", 120.50)

    def test_shipping_charged_negative(self):
        validate_event("shipping_charged", -35.00)

    def test_refund_created_negative(self):
        validate_event("refund_created", -800.00)

    def test_partial_refund_negative(self):
        validate_event("partial_refund", -200.00)

    def test_charged_back_negative(self):
        validate_event("charged_back", -1000.00)

    def test_adjustment_fee_negative(self):
        validate_event("adjustment_fee", -5.50)

    def test_adjustment_shipping_negative(self):
        validate_event("adjustment_shipping", -3.20)

    def test_adjustment_fee_rejects_positive(self):
        with pytest.raises(ValueError, match="negative"):
            validate_event("adjustment_fee", 5.50)

    # --- Zero events (flags) ---

    def test_ca_sync_completed_zero(self):
        validate_event("ca_sync_completed", 0)

    def test_ca_sync_completed_rejects_nonzero(self):
        with pytest.raises(ValueError, match="zero"):
            validate_event("ca_sync_completed", 100.00)

    def test_ca_sync_failed_zero(self):
        validate_event("ca_sync_failed", 0)

    def test_money_released_zero(self):
        validate_event("money_released", 0)

    def test_mediation_opened_zero(self):
        validate_event("mediation_opened", 0)

    # --- Unknown types ---

    def test_unknown_type_raises(self):
        with pytest.raises(ValueError, match="Unknown event_type"):
            validate_event("invalid_type", 0)

    # --- Edge: zero amount on financial events ---

    def test_sale_approved_zero_is_valid(self):
        """Zero amount sale — technically valid (free item)."""
        validate_event("sale_approved", 0)

    def test_fee_charged_zero_is_valid(self):
        """Zero fee — no commission charged."""
        validate_event("fee_charged", 0)

    def test_refund_created_zero_is_valid(self):
        """Zero refund amount — edge case but valid."""
        validate_event("refund_created", 0)


# ===========================================================================
# EVENT_TYPES coverage
# ===========================================================================

class TestEventTypesCoverage:
    """Ensure all expected event types are defined."""

    EXPECTED_TYPES = [
        "sale_approved", "fee_charged", "shipping_charged",
        "subsidy_credited", "refund_created", "refund_fee",
        "refund_shipping", "partial_refund",
        "ca_sync_completed", "ca_sync_failed",
        "money_released", "mediation_opened",
        "charged_back", "reimbursed",
        "adjustment_fee", "adjustment_shipping",
        "cash_release", "cash_expense", "cash_income",
        "cash_transfer_out", "cash_transfer_in", "cash_internal",
    ]

    def test_all_types_defined(self):
        for et in self.EXPECTED_TYPES:
            assert et in EVENT_TYPES, f"Missing event type: {et}"

    def test_no_extra_types(self):
        """No undocumented types snuck in."""
        for et in EVENT_TYPES:
            assert et in self.EXPECTED_TYPES, f"Undocumented event type: {et}"

    def test_positive_types(self):
        positive = [k for k, v in EVENT_TYPES.items() if v == "positive"]
        assert set(positive) == {
            "sale_approved", "subsidy_credited",
            "refund_fee", "refund_shipping", "reimbursed",
            "cash_release", "cash_income", "cash_transfer_in",
        }

    def test_negative_types(self):
        negative = [k for k, v in EVENT_TYPES.items() if v == "negative"]
        assert set(negative) == {
            "fee_charged", "shipping_charged",
            "refund_created", "partial_refund", "charged_back",
            "adjustment_fee", "adjustment_shipping",
            "cash_expense", "cash_transfer_out",
        }

    def test_zero_types(self):
        zero = [k for k, v in EVENT_TYPES.items() if v == "zero"]
        assert set(zero) == {
            "ca_sync_completed", "ca_sync_failed",
            "money_released", "mediation_opened",
        }

    def test_type_count(self):
        assert len(EVENT_TYPES) == 22


# ===========================================================================
# Idempotency key uniqueness scenarios
# ===========================================================================

class TestIdempotencyScenarios:
    """Real-world scenarios that must produce unique keys."""

    def test_approved_events_for_same_payment(self):
        """sale + fee + shipping for same payment = 3 different keys."""
        keys = {
            build_idempotency_key("141air", 100, "sale_approved"),
            build_idempotency_key("141air", 100, "fee_charged"),
            build_idempotency_key("141air", 100, "shipping_charged"),
        }
        assert len(keys) == 3

    def test_refund_events_for_same_payment(self):
        """refund + refund_fee + refund_shipping = 3 different keys."""
        keys = {
            build_idempotency_key("141air", 100, "refund_created"),
            build_idempotency_key("141air", 100, "refund_fee"),
            build_idempotency_key("141air", 100, "refund_shipping"),
        }
        assert len(keys) == 3

    def test_full_lifecycle_unique_keys(self):
        """Complete lifecycle of a payment that gets refunded."""
        payment_id = 142000000000
        events = [
            ("sale_approved", ""),
            ("fee_charged", ""),
            ("shipping_charged", ""),
            ("ca_sync_completed", ""),
            ("money_released", ""),
            ("refund_created", ""),
            ("refund_fee", ""),
            ("refund_shipping", ""),
        ]
        keys = {
            build_idempotency_key("141air", payment_id, et, suffix)
            for et, suffix in events
        }
        assert len(keys) == len(events)

    def test_multiple_partial_refunds(self):
        """3 partial refunds on same payment = 3 unique keys."""
        keys = {
            build_idempotency_key("141air", 100, "partial_refund", suffix=str(i))
            for i in range(3)
        }
        assert len(keys) == 3

    def test_same_payment_different_sellers(self):
        """Same ML payment_id processed by different sellers (shouldn't happen, but safe)."""
        k1 = build_idempotency_key("141air", 100, "sale_approved")
        k2 = build_idempotency_key("netair", 100, "sale_approved")
        assert k1 != k2


# ===========================================================================
# derive_payment_status
# ===========================================================================

class TestDerivePaymentStatus:
    """Centralized status derivation from event types."""

    def test_error_takes_priority(self):
        """ca_sync_failed wins even if ca_sync_completed is also present."""
        assert derive_payment_status({"sale_approved", "ca_sync_completed", "ca_sync_failed"}) == "error"

    def test_error_alone(self):
        assert derive_payment_status({"sale_approved", "ca_sync_failed"}) == "error"

    def test_refunded_via_refund_created(self):
        assert derive_payment_status({"sale_approved", "refund_created"}) == "refunded"

    def test_refunded_via_charged_back(self):
        assert derive_payment_status({"sale_approved", "charged_back"}) == "refunded"

    def test_refunded_beats_synced(self):
        """Refund after sync = still refunded."""
        assert derive_payment_status({"sale_approved", "ca_sync_completed", "refund_created"}) == "refunded"

    def test_synced(self):
        assert derive_payment_status({"sale_approved", "fee_charged", "ca_sync_completed"}) == "synced"

    def test_queued(self):
        assert derive_payment_status({"sale_approved", "fee_charged"}) == "queued"

    def test_queued_minimal(self):
        assert derive_payment_status({"sale_approved"}) == "queued"

    def test_unknown_no_events(self):
        assert derive_payment_status(set()) == "unknown"

    def test_unknown_only_flags(self):
        """Only operational flags, no sale_approved."""
        assert derive_payment_status({"money_released"}) == "unknown"

    def test_full_lifecycle_approved_synced(self):
        events = {"sale_approved", "fee_charged", "shipping_charged", "ca_sync_completed", "money_released"}
        assert derive_payment_status(events) == "synced"

    def test_full_lifecycle_refunded(self):
        events = {"sale_approved", "fee_charged", "ca_sync_completed", "refund_created", "refund_fee"}
        assert derive_payment_status(events) == "refunded"

    def test_error_with_refund_error_wins(self):
        """Error takes priority over refund."""
        events = {"sale_approved", "refund_created", "ca_sync_failed"}
        assert derive_payment_status(events) == "error"

    def test_chargeback_reimbursed_is_refunded(self):
        """charged_back + reimbursed: charged_back triggers refunded."""
        events = {"sale_approved", "charged_back", "reimbursed"}
        assert derive_payment_status(events) == "refunded"


# ===========================================================================
# EventRecordError
# ===========================================================================

class TestEventRecordError:
    def test_is_exception(self):
        assert issubclass(EventRecordError, Exception)

    def test_message(self):
        err = EventRecordError("DB error recording sale_approved for payment 123: connection refused")
        assert "sale_approved" in str(err)
        assert "123" in str(err)
