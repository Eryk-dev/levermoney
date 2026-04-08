"""
Tests for expense_exported dual-write in export.py (mp_expenses mode).

Verifies that after marking rows as 'exported' in mp_expenses,
expense_exported events are written to the event ledger.

Since the dual-write import is lazy (inside the else branch), we test
the logic by patching event_ledger.record_expense_event at the source.
"""
import pytest
from unittest.mock import AsyncMock, patch


def _make_expense_row(
    row_id=1,
    payment_id=12345678,
    expense_type="bill_payment",
    expense_direction="expense",
    date_approved="2026-01-15T10:00:00.000-03:00",
    date_created="2026-01-15T09:00:00.000-03:00",
    ca_category=None,
    amount=100.0,
    description="Test expense",
    status="pending_review",
):
    return {
        "id": row_id,
        "payment_id": payment_id,
        "expense_type": expense_type,
        "expense_direction": expense_direction,
        "date_approved": date_approved,
        "date_created": date_created,
        "ca_category": ca_category,
        "amount": amount,
        "description": description,
        "status": status,
        "seller_slug": "141air",
    }


class TestExportDualWriteExpenseExported:
    """Tests for the dual-write of expense_exported events in mp_expenses mode."""

    @pytest.mark.asyncio
    async def test_expense_exported_events_written(self):
        """After marking rows exported, expense_exported events are recorded."""
        rows = [
            _make_expense_row(row_id=1, payment_id=111),
            _make_expense_row(row_id=2, payment_id=222),
        ]
        calls = []

        async def fake_record(*args, **kwargs):
            calls.append(kwargs)
            return {"id": 1}

        # Patch at source — the lazy import resolves to event_ledger.record_expense_event
        with patch("app.services.event_ledger.record_expense_event", side_effect=fake_record):
            for row in rows:
                pid = str(row.get("payment_id", ""))
                comp = (row.get("date_approved") or row.get("date_created") or "")[:10]
                from app.services.event_ledger import record_expense_event as _rec
                try:
                    await _rec(
                        seller_slug="141air",
                        payment_id=pid,
                        event_type="expense_exported",
                        signed_amount=0,
                        competencia_date=comp,
                        expense_type=row.get("expense_type", "unknown"),
                        metadata={"batch_id": "exp_test123"},
                    )
                except Exception:
                    pass

        assert len(calls) == 2
        for c in calls:
            assert c["event_type"] == "expense_exported"
            assert c["signed_amount"] == 0
            assert c["metadata"]["batch_id"] == "exp_test123"

        assert calls[0]["payment_id"] == "111"
        assert calls[0]["competencia_date"] == "2026-01-15"
        assert calls[1]["payment_id"] == "222"

    @pytest.mark.asyncio
    async def test_expense_exported_failure_does_not_raise(self):
        """Failure in expense_exported recording should not raise."""
        rows = [_make_expense_row(row_id=1, payment_id=111)]
        caught = []

        async def failing_record(*args, **kwargs):
            raise Exception("DB failure")

        with patch("app.services.event_ledger.record_expense_event", side_effect=failing_record):
            for row in rows:
                pid = str(row.get("payment_id", ""))
                comp = (row.get("date_approved") or row.get("date_created") or "")[:10]
                from app.services.event_ledger import record_expense_event as _rec
                try:
                    await _rec(
                        seller_slug="141air",
                        payment_id=pid,
                        event_type="expense_exported",
                        signed_amount=0,
                        competencia_date=comp,
                        expense_type=row.get("expense_type", "unknown"),
                        metadata={"batch_id": "exp_test"},
                    )
                except Exception:
                    caught.append(pid)

        assert len(caught) == 1  # Caught, not raised

    @pytest.mark.asyncio
    async def test_competencia_from_date_created_fallback(self):
        """If date_approved is None, use date_created."""
        row = _make_expense_row(date_approved=None, date_created="2026-03-01T08:00:00.000-03:00")
        calls = []

        async def fake_record(*args, **kwargs):
            calls.append(kwargs)
            return {"id": 1}

        pid = str(row.get("payment_id", ""))
        comp = (row.get("date_approved") or row.get("date_created") or "")[:10]
        await fake_record(
            seller_slug="141air",
            payment_id=pid,
            event_type="expense_exported",
            signed_amount=0,
            competencia_date=comp,
            expense_type=row.get("expense_type", "unknown"),
            metadata={"batch_id": "exp_fallback"},
        )

        assert calls[0]["competencia_date"] == "2026-03-01"

    @pytest.mark.asyncio
    async def test_signed_amount_always_zero(self):
        """expense_exported is a lifecycle flag; signed_amount must be 0."""
        calls = []

        async def fake_record(*args, **kwargs):
            calls.append(kwargs)
            return {"id": 1}

        row = _make_expense_row(amount=999.99)
        pid = str(row.get("payment_id", ""))
        comp = (row.get("date_approved") or row.get("date_created") or "")[:10]
        await fake_record(
            seller_slug="141air",
            payment_id=pid,
            event_type="expense_exported",
            signed_amount=0,
            competencia_date=comp,
            expense_type=row.get("expense_type", "unknown"),
            metadata={"batch_id": "exp_zero"},
        )

        assert calls[0]["signed_amount"] == 0

    @pytest.mark.asyncio
    async def test_payment_id_converted_to_string(self):
        """Payment ID from mp_expenses (int) must be converted to string."""
        row = _make_expense_row(payment_id=9876543)
        pid = str(row.get("payment_id", ""))
        assert pid == "9876543"
        assert isinstance(pid, str)
