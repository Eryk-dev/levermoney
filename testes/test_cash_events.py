"""
Tests for cash event types, idempotency, and query protection.

Validates:
- Cash event types exist in EVENT_TYPES
- cash_internal accepts any sign
- record_cash_event idempotency key includes date and abbreviation
- get_dre_summary and get_payment_statuses exclude cash events
- get_cash_summary aggregates correctly
- SKIP_TO_CASH_TYPE does not blanket-map everything to cash_internal

Run: python3 -m pytest testes/test_cash_events.py -v
"""
import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from types import SimpleNamespace

from app.services.event_ledger import (
    EVENT_TYPES,
    validate_event,
    record_cash_event,
    get_dre_summary,
    get_payment_statuses,
    get_cash_summary,
    CASH_TYPE_MAP,
    SKIP_TO_CASH_TYPE,
    SKIP_ABBREV,
)


def _resp(data=None):
    return SimpleNamespace(data=data or [])


class TestCashEvents:

    def test_cash_event_types_exist(self):
        """All 6 cash types must be in EVENT_TYPES."""
        expected = [
            "cash_release", "cash_expense", "cash_income",
            "cash_transfer_out", "cash_transfer_in", "cash_internal",
        ]
        for et in expected:
            assert et in EVENT_TYPES, f"Missing cash event type: {et}"

    def test_validate_cash_any_sign(self):
        """cash_internal accepts positive, negative, and zero."""
        validate_event("cash_internal", 100.0)
        validate_event("cash_internal", -100.0)
        validate_event("cash_internal", 0)

    @pytest.mark.asyncio
    async def test_record_cash_event_idempotency_includes_date(self):
        """Same ref_id on different dates produces different idempotency keys."""
        captured_keys = []

        async def fake_record_event(**kwargs):
            captured_keys.append(kwargs["idempotency_key"])
            return {"id": 1}

        with patch("app.services.event_ledger.record_event", side_effect=fake_record_event):
            await record_cash_event(
                seller_slug="141air", reference_id="12345",
                event_type="cash_release", signed_amount=100.0,
                event_date="2026-01-01", extrato_type="liberacao",
                expense_type_abbrev="cr",
            )
            await record_cash_event(
                seller_slug="141air", reference_id="12345",
                event_type="cash_release", signed_amount=100.0,
                event_date="2026-01-02", extrato_type="liberacao",
                expense_type_abbrev="cr",
            )

        assert len(captured_keys) == 2
        assert captured_keys[0] != captured_keys[1]
        assert "2026-01-01" in captured_keys[0]
        assert "2026-01-02" in captured_keys[1]

    @pytest.mark.asyncio
    async def test_record_cash_event_idempotency_includes_abbrev(self):
        """Same ref_id/date with different abbreviations produces different keys."""
        captured_keys = []

        async def fake_record_event(**kwargs):
            captured_keys.append(kwargs["idempotency_key"])
            return {"id": 1}

        with patch("app.services.event_ledger.record_event", side_effect=fake_record_event):
            await record_cash_event(
                seller_slug="141air", reference_id="12345",
                event_type="cash_expense", signed_amount=-50.0,
                event_date="2026-01-10", extrato_type="difal",
                expense_type_abbrev="df",
            )
            await record_cash_event(
                seller_slug="141air", reference_id="12345",
                event_type="cash_expense", signed_amount=-30.0,
                event_date="2026-01-10", extrato_type="faturas_ml",
                expense_type_abbrev="fm",
            )

        assert len(captured_keys) == 2
        assert captured_keys[0] != captured_keys[1]
        assert captured_keys[0].endswith(":df")
        assert captured_keys[1].endswith(":fm")

    @pytest.mark.asyncio
    async def test_no_collision_different_types_same_ref(self):
        """Two expense types with same ref_id/day do not collide."""
        captured_keys = []

        async def fake_record_event(**kwargs):
            captured_keys.append(kwargs["idempotency_key"])
            return {"id": 1}

        with patch("app.services.event_ledger.record_event", side_effect=fake_record_event):
            await record_cash_event(
                seller_slug="141air", reference_id="99999",
                event_type="cash_expense", signed_amount=-100.0,
                event_date="2026-01-15", extrato_type="subscription",
                expense_type_abbrev="sb",
            )
            await record_cash_event(
                seller_slug="141air", reference_id="99999",
                event_type="cash_expense", signed_amount=-200.0,
                event_date="2026-01-15", extrato_type="pagamento_cartao_credito",
                expense_type_abbrev="cc",
            )

        assert len(set(captured_keys)) == 2

    @pytest.mark.asyncio
    async def test_get_dre_summary_excludes_cash(self):
        """get_dre_summary must filter out cash_* and expense_* event types."""
        rows = [
            {"event_type": "sale_approved", "signed_amount": 500.0},
            {"event_type": "fee_charged", "signed_amount": -50.0},
            {"event_type": "cash_release", "signed_amount": 500.0},
            {"event_type": "cash_expense", "signed_amount": -100.0},
            {"event_type": "expense_classified", "signed_amount": -30.0},
        ]
        mock_db = MagicMock()
        chain = mock_db.table.return_value.select.return_value
        chain.eq.return_value.gte.return_value.lte.return_value.range.return_value.execute.return_value = _resp(rows)

        with patch("app.services.event_ledger.get_db", return_value=mock_db):
            summary = await get_dre_summary("141air", "2026-01-01", "2026-01-31")

        assert "sale_approved" in summary
        assert "fee_charged" in summary
        assert "cash_release" not in summary
        assert "cash_expense" not in summary
        assert "expense_classified" not in summary

    @pytest.mark.asyncio
    async def test_get_payment_statuses_excludes_cash(self):
        """get_payment_statuses must filter out cash_* and expense_* rows."""
        rows = [
            {"ml_payment_id": 100, "event_type": "sale_approved"},
            {"ml_payment_id": 100, "event_type": "ca_sync_completed"},
            {"ml_payment_id": 0, "event_type": "cash_release"},
            {"ml_payment_id": 0, "event_type": "cash_expense"},
        ]
        mock_db = MagicMock()
        chain = mock_db.table.return_value.select.return_value
        chain.eq.return_value.range.return_value.execute.return_value = _resp(rows)

        with patch("app.services.event_ledger.get_db", return_value=mock_db):
            statuses = await get_payment_statuses("141air")

        assert 100 in statuses
        assert statuses[100] == "synced"
        assert 0 not in statuses

    @pytest.mark.asyncio
    async def test_get_cash_summary(self):
        """get_cash_summary aggregates only cash_* events by type."""
        rows = [
            {"event_type": "cash_release", "signed_amount": 100.0},
            {"event_type": "cash_release", "signed_amount": 200.0},
            {"event_type": "cash_expense", "signed_amount": -50.0},
            {"event_type": "sale_approved", "signed_amount": 999.0},
        ]
        mock_db = MagicMock()
        chain = mock_db.table.return_value.select.return_value
        chain.eq.return_value.gte.return_value.lte.return_value.range.return_value.execute.return_value = _resp(rows)

        with patch("app.services.event_ledger.get_db", return_value=mock_db):
            summary = await get_cash_summary("141air", "2026-01-01", "2026-01-31")

        assert summary["cash_release"] == 300.0
        assert summary["cash_expense"] == -50.0
        assert "sale_approved" not in summary

    def test_skip_mapping_not_all_internal(self):
        """PIX enviado must be cash_transfer_out, NOT cash_internal."""
        assert SKIP_TO_CASH_TYPE["pix enviado"] == "cash_transfer_out"
        assert SKIP_TO_CASH_TYPE["pix enviado"] != "cash_internal"
        # Also verify other non-internal skips
        assert SKIP_TO_CASH_TYPE["transferencia pix"] == "cash_transfer_out"
        assert SKIP_TO_CASH_TYPE["compra mercado libre"] == "cash_expense"
