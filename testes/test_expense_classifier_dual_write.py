"""
Tests for expense event writes in expense_classifier.py.

Verifies that classify_non_order_payment() writes expense_captured
(and expense_classified if auto-categorized) to the event ledger.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.expense_classifier import (
    classify_non_order_payment,
    _expense_signed_amount,
    _expense_competencia_date,
    _build_expense_metadata,
)


# ── Helpers ──────────────────────────────────────────────────────────────

def _make_payment(
    pid=12345, amount=49.90, description="Pagamento de boleto",
    operation_type="regular_payment", status="approved",
    date_approved="2026-01-15T10:00:00.000-04:00",
    date_created="2026-01-14T09:00:00.000-04:00",
    payment_method_id="bolbradesco",
    external_reference="REF123",
    branch="Bill Payment",
    order_id=None,
    **overrides,
):
    payment = {
        "id": pid,
        "status": status,
        "transaction_amount": amount,
        "description": description,
        "operation_type": operation_type,
        "date_approved": date_approved,
        "date_created": date_created,
        "payment_method_id": payment_method_id,
        "external_reference": external_reference,
        "point_of_interaction": {
            "business_info": {"branch": branch, "unit": ""},
            "transaction_data": {"references": [], "bank_info": {}},
        },
        "payer": {"identification": {"type": "CPF", "number": "12345678901"}},
    }
    if order_id:
        payment["order"] = {"id": order_id}
    payment.update(overrides)
    return payment


def _mock_db():
    """Create a mock DB (no longer used for mp_expenses, kept for signature compat)."""
    return MagicMock()


# ── Pure function tests ──────────────────────────────────────────────────

class TestExpenseSignedAmount:
    def test_income_positive(self):
        assert _expense_signed_amount("income", 100.0) == 100.0

    def test_expense_negative(self):
        assert _expense_signed_amount("expense", 49.90) == -49.90

    def test_transfer_negative(self):
        assert _expense_signed_amount("transfer", 200.0) == -200.0

    def test_income_abs(self):
        """Even if amount is negative, income returns positive."""
        assert _expense_signed_amount("income", -50.0) == 50.0


class TestExpenseCompetenciaDate:
    def test_from_date_approved(self):
        p = {"date_approved": "2026-01-15T10:00:00.000-04:00", "date_created": "2026-01-14T09:00:00"}
        assert _expense_competencia_date(p) == "2026-01-15"

    def test_fallback_date_created(self):
        p = {"date_approved": None, "date_created": "2026-01-14T09:00:00.000-04:00"}
        assert _expense_competencia_date(p) == "2026-01-14"

    def test_no_dates(self):
        p = {}
        assert _expense_competencia_date(p) == ""


class TestBuildExpenseMetadata:
    def test_all_fields_present(self):
        payment = _make_payment()
        meta = _build_expense_metadata(
            "bill_payment", "expense", None, False, "Boleto - test", payment,
        )
        expected_keys = {
            "expense_type", "expense_direction", "ca_category",
            "auto_categorized", "description", "amount",
            "date_created", "date_approved", "business_branch",
            "operation_type", "payment_method", "external_reference",
            "beneficiary_name", "notes",
        }
        assert set(meta.keys()) == expected_keys
        assert meta["expense_type"] == "bill_payment"
        assert meta["expense_direction"] == "expense"
        assert meta["amount"] == 49.90
        assert meta["payment_method"] == "bolbradesco"
        assert meta["business_branch"] == "Bill Payment"


# ── Event ledger write tests ───────────────────────────────────────────

class TestExpenseClassifierEventWrites:
    """Tests that classify_non_order_payment writes to event ledger."""

    @pytest.mark.asyncio
    async def test_expense_captured_written_for_pending_review(self):
        """Non-auto payment → expense_captured only, no expense_classified."""
        db = _mock_db()
        payment = _make_payment(
            pid=99001, description="Boleto generico", branch="Bill Payment",
        )
        calls = []

        async def fake_record(*args, **kwargs):
            calls.append(kwargs)
            return {"id": 1}

        with patch("app.services.expense_classifier.record_expense_event", side_effect=fake_record):
            result = await classify_non_order_payment(db, "141air", payment)

        assert result is not None
        # Should have exactly 1 call: expense_captured
        assert len(calls) == 1
        assert calls[0]["event_type"] == "expense_captured"
        assert calls[0]["seller_slug"] == "141air"
        assert calls[0]["payment_id"] == "99001"
        assert calls[0]["signed_amount"] < 0  # expense → negative
        assert calls[0]["competencia_date"] == "2026-01-15"
        assert calls[0]["expense_type"] == "bill_payment"
        # Metadata must have all required fields
        meta = calls[0]["metadata"]
        assert meta["expense_direction"] == "expense"
        assert meta["auto_categorized"] is False
        assert meta["amount"] == 49.90

    @pytest.mark.asyncio
    async def test_expense_captured_and_classified_for_auto(self):
        """Auto-categorized payment → expense_captured + expense_classified."""
        db = _mock_db()
        payment = _make_payment(
            pid=99002, description="DARF imposto", branch="Bill Payment",
            operation_type="regular_payment",
        )
        calls = []

        async def fake_record(*args, **kwargs):
            calls.append(kwargs)
            return {"id": 1}

        with patch("app.services.expense_classifier.record_expense_event", side_effect=fake_record):
            result = await classify_non_order_payment(db, "141air", payment)

        assert result is not None
        assert len(calls) == 2
        assert calls[0]["event_type"] == "expense_captured"
        assert calls[1]["event_type"] == "expense_classified"
        assert calls[1]["signed_amount"] == 0
        assert calls[1]["metadata"]["ca_category"] == "2.2.7 Simples Nacional"

    @pytest.mark.asyncio
    async def test_income_has_positive_signed_amount(self):
        """Cashback (income direction) → positive signed_amount."""
        db = _mock_db()
        payment = _make_payment(
            pid=99003, amount=15.50, description="Cashback flex",
            operation_type="money_transfer", branch="Cashback",
        )
        calls = []

        async def fake_record(*args, **kwargs):
            calls.append(kwargs)
            return {"id": 1}

        with patch("app.services.expense_classifier.record_expense_event", side_effect=fake_record):
            result = await classify_non_order_payment(db, "141air", payment)

        assert result is not None
        captured = calls[0]
        assert captured["signed_amount"] == 15.50  # positive for income
        assert captured["metadata"]["expense_direction"] == "income"

    @pytest.mark.asyncio
    async def test_skip_direction_no_write(self):
        """Skipped payments (partition_transfer) → no event writes."""
        db = _mock_db()
        payment = _make_payment(
            pid=99004, operation_type="partition_transfer", branch="other",
        )
        calls = []

        async def fake_record(*args, **kwargs):
            calls.append(kwargs)
            return {"id": 1}

        with patch("app.services.expense_classifier.record_expense_event", side_effect=fake_record):
            result = await classify_non_order_payment(db, "141air", payment)

        assert result is None
        assert len(calls) == 0

    @pytest.mark.asyncio
    async def test_event_failure_still_returns_data(self):
        """EventRecordError on write → warning logged, classification still returned."""
        from app.services.event_ledger import EventRecordError

        db = _mock_db()
        payment = _make_payment(pid=99005, description="Boleto test", branch="Bill Payment")

        async def failing_record(*args, **kwargs):
            raise EventRecordError("DB failure")

        with patch("app.services.expense_classifier.record_expense_event", side_effect=failing_record):
            result = await classify_non_order_payment(db, "141air", payment)

        assert result is not None
        assert result["payment_id"] == 99005

    @pytest.mark.asyncio
    async def test_return_dict_has_classification_fields(self):
        """Return dict should contain all classification fields."""
        db = _mock_db()
        payment = _make_payment(pid=99006)
        calls = []

        async def fake_record(*args, **kwargs):
            calls.append(kwargs)
            return {"id": 1}

        with patch("app.services.expense_classifier.record_expense_event", side_effect=fake_record):
            result = await classify_non_order_payment(db, "141air", payment)

        assert result["seller_slug"] == "141air"
        assert result["payment_id"] == 99006
        assert "expense_type" in result
        assert "expense_direction" in result
        assert "ca_category" in result
        assert "auto_categorized" in result
        assert "description" in result
        assert "amount" in result

    @pytest.mark.asyncio
    async def test_metadata_has_all_required_fields(self):
        """Verify metadata contains all fields needed for expense reads."""
        db = _mock_db()
        payment = _make_payment(pid=99007)
        calls = []

        async def fake_record(*args, **kwargs):
            calls.append(kwargs)
            return {"id": 1}

        with patch("app.services.expense_classifier.record_expense_event", side_effect=fake_record):
            await classify_non_order_payment(db, "141air", payment)

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
    async def test_classified_failure_does_not_block(self):
        """expense_classified failure does not prevent return."""
        from app.services.event_ledger import EventRecordError

        db = _mock_db()
        # DARF → auto_categorized = True → will try expense_classified
        payment = _make_payment(pid=99008, description="DARF 1234", branch="Bill Payment")
        call_count = 0

        async def selective_fail(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if kwargs.get("event_type") == "expense_classified":
                raise EventRecordError("classified fail")
            return {"id": 1}

        with patch("app.services.expense_classifier.record_expense_event", side_effect=selective_fail):
            result = await classify_non_order_payment(db, "141air", payment)

        assert result is not None
        assert call_count == 2  # both attempted
