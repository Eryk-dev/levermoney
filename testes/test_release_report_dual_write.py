"""
Tests for dual-write in release_report_sync.py.

Verifies that sync_release_report() and backfill_release_report()
write expense_captured (and expense_classified if auto-categorized)
to the event ledger after inserting to mp_expenses.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.release_report_sync import (
    _release_signed_amount,
    _build_release_expense_metadata,
    _dual_write_release_expense_events,
)


# ── Test data helpers ────────────────────────────────────────────────────

def _make_row(
    source_id="987654321",
    date="2026-01-15T10:00:00.000-03:00",
    description="payout",
    net_credit=0.0,
    net_debit=500.0,
    gross_amount=500.0,
    external_reference="",
    payment_method="",
    payout_bank_account="12345",
    order_id="",
    pack_id="",
):
    return {
        "source_id": source_id,
        "date": date,
        "description": description,
        "net_credit": net_credit,
        "net_debit": net_debit,
        "gross_amount": gross_amount,
        "external_reference": external_reference,
        "payment_method": payment_method,
        "payout_bank_account": payout_bank_account,
        "order_id": order_id,
        "pack_id": pack_id,
    }


# ── Pure function tests ──────────────────────────────────────────────────

class TestReleaseSignedAmount:
    def test_income_positive(self):
        assert _release_signed_amount("income", 100.0) == 100.0

    def test_expense_negative(self):
        assert _release_signed_amount("expense", 50.0) == -50.0

    def test_transfer_negative(self):
        assert _release_signed_amount("transfer", 200.0) == -200.0

    def test_income_abs(self):
        """Even if amount is negative, income returns positive."""
        assert _release_signed_amount("income", -50.0) == 50.0


class TestBuildReleaseExpenseMetadata:
    def test_payout_all_fields(self):
        row = _make_row()
        meta = _build_release_expense_metadata(
            row, "transfer_pix", "transfer", None, False,
            "Saque PIX p/ conta 12345 - R$ 500.0", 500.0,
        )
        expected_keys = {
            "expense_type", "expense_direction", "ca_category",
            "auto_categorized", "description", "amount",
            "date_created", "date_approved", "business_branch",
            "operation_type", "payment_method", "external_reference",
            "beneficiary_name", "notes",
        }
        assert set(meta.keys()) == expected_keys
        assert meta["expense_type"] == "transfer_pix"
        assert meta["expense_direction"] == "transfer"
        assert meta["ca_category"] is None
        assert meta["auto_categorized"] is False
        assert meta["amount"] == 500.0
        assert meta["date_created"] == row["date"]
        assert meta["date_approved"] == row["date"]
        assert meta["business_branch"] is None
        assert meta["operation_type"] == "release_payout"
        assert meta["payment_method"] is None  # empty string becomes None
        assert meta["external_reference"] is None
        assert meta["beneficiary_name"] is None
        assert meta["notes"] == "12345"  # payout_bank_account

    def test_cashback_auto_categorized(self):
        row = _make_row(description="cashback", net_credit=25.0, net_debit=0.0)
        meta = _build_release_expense_metadata(
            row, "cashback", "income",
            "1.3.4 Descontos e Estornos de Taxas e Tarifas", True,
            "Cashback ML (release) - 987654321 R$ 25.0", 25.0,
        )
        assert meta["auto_categorized"] is True
        assert meta["ca_category"] == "1.3.4 Descontos e Estornos de Taxas e Tarifas"
        assert meta["operation_type"] == "release_cashback"

    def test_darf_expense(self):
        row = _make_row(description="payout", net_debit=125.0)
        meta = _build_release_expense_metadata(
            row, "darf", "expense",
            "2.2.7 Simples Nacional", True,
            "DARF (release, lote) - R$ 125.0", 125.0,
        )
        assert meta["expense_type"] == "darf"
        assert meta["auto_categorized"] is True


# ── Dual-write helper tests ─────────────────────────────────────────────

class TestDualWriteReleaseExpenseEvents:
    @pytest.mark.asyncio
    async def test_captured_only_when_no_category(self):
        """No auto-category → only expense_captured, no expense_classified."""
        row = _make_row()
        calls = []

        async def fake_record(*args, **kwargs):
            calls.append(kwargs)
            return {"id": 1}

        with patch("app.services.release_report_sync.record_expense_event", side_effect=fake_record):
            await _dual_write_release_expense_events(
                "141air", "987654321", "transfer_pix", "transfer",
                None, False, row,
                "Saque PIX p/ conta 12345 - R$ 500.0", 500.0,
            )

        assert len(calls) == 1
        assert calls[0]["event_type"] == "expense_captured"
        assert calls[0]["seller_slug"] == "141air"
        assert calls[0]["payment_id"] == "987654321"
        assert calls[0]["signed_amount"] == -500.0
        assert calls[0]["competencia_date"] == "2026-01-15"
        assert calls[0]["expense_type"] == "transfer_pix"

    @pytest.mark.asyncio
    async def test_captured_and_classified_when_auto(self):
        """Auto-categorized → expense_captured + expense_classified."""
        row = _make_row(description="cashback", net_credit=25.0, net_debit=0.0)
        calls = []

        async def fake_record(*args, **kwargs):
            calls.append(kwargs)
            return {"id": 1}

        with patch("app.services.release_report_sync.record_expense_event", side_effect=fake_record):
            await _dual_write_release_expense_events(
                "141air", "111222333", "cashback", "income",
                "1.3.4 Descontos e Estornos de Taxas e Tarifas", True, row,
                "Cashback ML (release) - 111222333 R$ 25.0", 25.0,
            )

        assert len(calls) == 2
        assert calls[0]["event_type"] == "expense_captured"
        assert calls[0]["signed_amount"] == 25.0  # income → positive
        assert calls[1]["event_type"] == "expense_classified"
        assert calls[1]["signed_amount"] == 0
        assert calls[1]["metadata"]["ca_category"] == "1.3.4 Descontos e Estornos de Taxas e Tarifas"

    @pytest.mark.asyncio
    async def test_income_positive_signed_amount(self):
        """Income direction → positive signed_amount."""
        row = _make_row(description="shipping", net_credit=15.0, net_debit=0.0)
        calls = []

        async def fake_record(*args, **kwargs):
            calls.append(kwargs)
            return {"id": 1}

        with patch("app.services.release_report_sync.record_expense_event", side_effect=fake_record):
            await _dual_write_release_expense_events(
                "141air", "555666", "cashback", "income",
                "1.3.4 Descontos e Estornos de Taxas e Tarifas", True, row,
                "Bonus envio ML (release)", 15.0,
            )

        assert calls[0]["signed_amount"] == 15.0

    @pytest.mark.asyncio
    async def test_failure_logged_as_warning(self):
        """EventRecordError → warning logged, no exception raised."""
        from app.services.event_ledger import EventRecordError

        row = _make_row()

        async def failing_record(*args, **kwargs):
            raise EventRecordError("DB failure")

        with patch("app.services.release_report_sync.record_expense_event", side_effect=failing_record):
            # Should NOT raise
            await _dual_write_release_expense_events(
                "141air", "987654321", "transfer_pix", "transfer",
                None, False, row,
                "Saque PIX", 500.0,
            )

    @pytest.mark.asyncio
    async def test_classified_failure_does_not_block(self):
        """expense_classified failure does not prevent expense_captured."""
        from app.services.event_ledger import EventRecordError

        row = _make_row(description="cashback", net_credit=25.0)
        call_count = 0

        async def selective_fail(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if kwargs.get("event_type") == "expense_classified":
                raise EventRecordError("classified fail")
            return {"id": 1}

        with patch("app.services.release_report_sync.record_expense_event", side_effect=selective_fail):
            await _dual_write_release_expense_events(
                "141air", "111", "cashback", "income",
                "cat-123", True, row,
                "Cashback ML", 25.0,
            )

        assert call_count == 2  # both attempted

    @pytest.mark.asyncio
    async def test_metadata_has_all_required_fields(self):
        """Verify metadata contains all fields needed for Fase 3."""
        row = _make_row()
        calls = []

        async def fake_record(*args, **kwargs):
            calls.append(kwargs)
            return {"id": 1}

        with patch("app.services.release_report_sync.record_expense_event", side_effect=fake_record):
            await _dual_write_release_expense_events(
                "141air", "987654321", "transfer_pix", "transfer",
                None, False, row,
                "Saque PIX p/ conta 12345 - R$ 500.0", 500.0,
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

    @pytest.mark.asyncio
    async def test_competencia_date_truncated_to_10(self):
        """Competencia date should be date-only (first 10 chars)."""
        row = _make_row(date="2026-02-28T23:59:59.999-03:00")
        calls = []

        async def fake_record(*args, **kwargs):
            calls.append(kwargs)
            return {"id": 1}

        with patch("app.services.release_report_sync.record_expense_event", side_effect=fake_record):
            await _dual_write_release_expense_events(
                "141air", "123", "transfer_pix", "transfer",
                None, False, row, "Test", 100.0,
            )

        assert calls[0]["competencia_date"] == "2026-02-28"
