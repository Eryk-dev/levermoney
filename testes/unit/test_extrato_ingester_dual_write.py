"""
Tests for expense event writes in extrato_ingester.py.

Verifies that ingest_extrato_for_seller() and ingest_extrato_from_csv()
write expense_captured (and expense_classified if auto-categorized) to
the event ledger.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.extrato_ingester import (
    _extrato_signed_amount,
    _build_extrato_expense_metadata,
    _write_extrato_expense_events,
)


# ── Test data helpers ────────────────────────────────────────────────────

def _make_tx(
    ref_id="123456789",
    amount=-50.0,
    tx_type="Diferença da aliquota ICMS",
    date="2026-01-15",
):
    return {
        "reference_id": ref_id,
        "amount": amount,
        "transaction_type": tx_type,
        "date": date,
        "balance": 1000.0,
    }


# ── Pure function tests ──────────────────────────────────────────────────

class TestExtratoSignedAmount:
    def test_income_positive(self):
        assert _extrato_signed_amount("income", 100.0) == 100.0

    def test_expense_negative(self):
        assert _extrato_signed_amount("expense", 50.0) == -50.0

    def test_transfer_negative(self):
        assert _extrato_signed_amount("transfer", 200.0) == -200.0

    def test_income_abs(self):
        """Even if amount is negative, income returns positive."""
        assert _extrato_signed_amount("income", -50.0) == 50.0


class TestBuildExtratoExpenseMetadata:
    def test_all_fields_present(self):
        tx = _make_tx()
        meta = _build_extrato_expense_metadata(
            tx, "difal", "expense", "uuid-123", "DIFAL - Ref 123456789",
        )
        expected_keys = {
            "expense_type", "expense_direction", "ca_category",
            "auto_categorized", "description", "amount",
            "date_created", "date_approved", "business_branch",
            "operation_type", "payment_method", "external_reference",
            "beneficiary_name", "notes",
        }
        assert set(meta.keys()) == expected_keys
        assert meta["expense_type"] == "difal"
        assert meta["expense_direction"] == "expense"
        assert meta["ca_category"] == "uuid-123"
        assert meta["auto_categorized"] is True
        assert meta["amount"] == 50.0  # abs of -50.0
        assert meta["date_created"] == "2026-01-15"
        assert meta["date_approved"] == "2026-01-15"
        assert meta["business_branch"] is None
        assert meta["operation_type"] == "extrato_difal"
        assert meta["payment_method"] is None
        assert meta["external_reference"] == "123456789"
        assert meta["beneficiary_name"] is None
        assert meta["notes"] == "Diferença da aliquota ICMS"

    def test_no_category_not_auto(self):
        tx = _make_tx()
        meta = _build_extrato_expense_metadata(
            tx, "other", "expense", None, "Other - Ref 123456789",
        )
        assert meta["ca_category"] is None
        assert meta["auto_categorized"] is False


# ── Dual-write helper tests ─────────────────────────────────────────────

class TestDualWriteExtratoExpenseEvents:
    @pytest.mark.asyncio
    async def test_expense_captured_only_when_no_category(self):
        """No auto-category → only expense_captured, no expense_classified."""
        tx = _make_tx(amount=-75.0)
        calls = []

        async def fake_record(*args, **kwargs):
            calls.append(kwargs)
            return {"id": 1}

        with patch("app.services.extrato_ingester.record_expense_event", side_effect=fake_record):
            await _write_extrato_expense_events(
                "141air", "123456789:df", "difal", "expense",
                None, tx, "DIFAL - Ref 123456789",
            )

        assert len(calls) == 1
        assert calls[0]["event_type"] == "expense_captured"
        assert calls[0]["seller_slug"] == "141air"
        assert calls[0]["payment_id"] == "123456789:df"
        assert calls[0]["signed_amount"] == -75.0
        assert calls[0]["competencia_date"] == "2026-01-15"
        assert calls[0]["expense_type"] == "difal"

    @pytest.mark.asyncio
    async def test_captured_and_classified_when_auto(self):
        """Auto-categorized → expense_captured + expense_classified."""
        tx = _make_tx(amount=-30.0)
        calls = []

        async def fake_record(*args, **kwargs):
            calls.append(kwargs)
            return {"id": 1}

        with patch("app.services.extrato_ingester.record_expense_event", side_effect=fake_record):
            await _write_extrato_expense_events(
                "141air", "999:cm", "faturas_ml", "expense",
                "uuid-cat-123", tx, "Fatura ML - Ref 999",
            )

        assert len(calls) == 2
        assert calls[0]["event_type"] == "expense_captured"
        assert calls[0]["signed_amount"] == -30.0
        assert calls[1]["event_type"] == "expense_classified"
        assert calls[1]["signed_amount"] == 0
        assert calls[1]["metadata"]["ca_category"] == "uuid-cat-123"

    @pytest.mark.asyncio
    async def test_income_positive_signed_amount(self):
        """Income direction (e.g. reembolso) → positive signed_amount."""
        tx = _make_tx(amount=25.0, tx_type="Reembolso Reclamações")
        calls = []

        async def fake_record(*args, **kwargs):
            calls.append(kwargs)
            return {"id": 1}

        with patch("app.services.extrato_ingester.record_expense_event", side_effect=fake_record):
            await _write_extrato_expense_events(
                "141air", "555:rd", "reembolso_disputa", "income",
                None, tx, "Reembolso - Ref 555",
            )

        assert calls[0]["signed_amount"] == 25.0
        assert calls[0]["metadata"]["expense_direction"] == "income"

    @pytest.mark.asyncio
    async def test_failure_logged_as_warning(self):
        """EventRecordError → warning logged, no exception raised."""
        from app.services.event_ledger import EventRecordError

        tx = _make_tx()

        async def failing_record(*args, **kwargs):
            raise EventRecordError("DB failure")

        with patch("app.services.extrato_ingester.record_expense_event", side_effect=failing_record):
            # Should NOT raise
            await _write_extrato_expense_events(
                "141air", "123:df", "difal", "expense",
                None, tx, "DIFAL - Ref 123",
            )

    @pytest.mark.asyncio
    async def test_classified_failure_does_not_block(self):
        """expense_classified failure does not prevent expense_captured."""
        from app.services.event_ledger import EventRecordError

        tx = _make_tx()
        call_count = 0

        async def selective_fail(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if kwargs.get("event_type") == "expense_classified":
                raise EventRecordError("classified fail")
            return {"id": 1}

        with patch("app.services.extrato_ingester.record_expense_event", side_effect=selective_fail):
            await _write_extrato_expense_events(
                "141air", "123:cm", "faturas_ml", "expense",
                "uuid-cat", tx, "Fatura ML - Ref 123",
            )

        assert call_count == 2  # both attempted

    @pytest.mark.asyncio
    async def test_metadata_has_all_required_fields(self):
        """Verify metadata contains all fields needed for Fase 3."""
        tx = _make_tx()
        calls = []

        async def fake_record(*args, **kwargs):
            calls.append(kwargs)
            return {"id": 1}

        with patch("app.services.extrato_ingester.record_expense_event", side_effect=fake_record):
            await _write_extrato_expense_events(
                "141air", "123:df", "difal", "expense",
                None, tx, "DIFAL - Ref 123",
            )

        meta = calls[0]["metadata"]
        required_keys = {
            "expense_type", "expense_direction", "ca_category",
            "auto_categorized", "description", "amount",
            "date_created", "date_approved", "business_branch",
            "operation_type", "payment_method", "external_reference",
            "beneficiary_name", "notes",
        }
        assert required_keys.issubset(set(meta.keys()))
