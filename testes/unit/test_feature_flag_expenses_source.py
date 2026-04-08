"""
Tests for financial_closing.py and extrato_coverage_checker.py expense reads.

Verifies that:
- _compute_manual_lane reads from event ledger via get_expense_list
- _lookup_expense_ids reads from payment_events

Run: python3 -m pytest testes/test_feature_flag_expenses_source.py -v
"""
import pytest
from unittest.mock import patch, AsyncMock, MagicMock


# ── Sample data ──────────────────────────────────────────────────


def _ledger_expense_rows():
    """Sample rows as returned by get_expense_list."""
    return [
        {
            "id": "1001",
            "payment_id": "1001",
            "amount": 50.0,
            "expense_direction": "expense",
            "expense_type": "difal",
            "ca_category": None,
            "auto_categorized": False,
            "description": "DIFAL",
            "status": "exported",
            "date_created": "2026-01-15T10:00:00.000-03:00",
            "date_approved": "2026-01-15T10:00:00.000-03:00",
            "business_branch": None,
            "operation_type": None,
            "payment_method": None,
            "external_reference": None,
            "febraban_code": None,
            "beneficiary_name": None,
            "notes": None,
            "exported_at": None,
            "created_at": "2026-01-15T13:00:00Z",
        },
        {
            "id": "1002",
            "payment_id": "1002",
            "amount": 30.0,
            "expense_direction": "income",
            "expense_type": "cashback",
            "ca_category": None,
            "auto_categorized": False,
            "description": "Cashback",
            "status": "pending_review",
            "date_created": "2026-01-15T11:00:00.000-03:00",
            "date_approved": "2026-01-15T11:00:00.000-03:00",
            "business_branch": None,
            "operation_type": None,
            "payment_method": None,
            "external_reference": None,
            "febraban_code": None,
            "beneficiary_name": None,
            "notes": None,
            "exported_at": None,
            "created_at": "2026-01-15T13:00:01Z",
        },
    ]


# ── Helpers ──────────────────────────────────────────────────────


def _mock_db_no_batches():
    """DB mock with batch_tables check failing."""
    mock_db = MagicMock()
    # For the batch_tables_available check
    mock_db.table.return_value.select.return_value.limit.return_value.execute.side_effect = Exception("no table")
    return mock_db


def _mock_db_for_expense_lookup(payment_id_field: str, found_ids: list[int]):
    """Build a mock DB that returns found_ids for a table lookup."""
    mock_db = MagicMock()

    def _make_result(ids):
        result = MagicMock()
        result.data = [{payment_id_field: pid} for pid in ids]
        return result

    # Chain: db.table(X).select(Y).eq().eq().in_().execute()
    chain = mock_db.table.return_value.select.return_value.eq.return_value
    chain.eq.return_value.in_.return_value.execute.return_value = _make_result(found_ids)
    return mock_db


# ── financial_closing: _compute_manual_lane ─────────────────────


@pytest.mark.asyncio
async def test_manual_lane_ledger_mode():
    """_compute_manual_lane calls get_expense_list."""
    ledger_rows = _ledger_expense_rows()

    with patch("app.services.event_ledger.get_expense_list", new_callable=AsyncMock, return_value=ledger_rows) as mock_gel:
        mock_db = _mock_db_no_batches()
        from app.services.financial_closing import _compute_manual_lane
        days, missing_ids, import_source = await _compute_manual_lane(
            mock_db, "test-seller", "2026-01-15", "2026-01-15"
        )

    # Should have called get_expense_list
    mock_gel.assert_called_once_with(
        "test-seller", date_from="2026-01-15", date_to="2026-01-15", limit=1_000_000,
    )
    assert len(days) == 1
    assert days[0]["date"] == "2026-01-15"
    assert days[0]["rows_total"] == 2


@pytest.mark.asyncio
async def test_manual_lane_ledger_exported_status():
    """_compute_manual_lane correctly identifies exported rows via status field."""
    ledger_rows = _ledger_expense_rows()  # row 1001 has status="exported"

    with patch("app.services.event_ledger.get_expense_list", new_callable=AsyncMock, return_value=ledger_rows):
        mock_db = _mock_db_no_batches()
        from app.services.financial_closing import _compute_manual_lane
        days, _, _ = await _compute_manual_lane(
            mock_db, "test-seller", "2026-01-15", "2026-01-15"
        )

    day = days[0]
    assert day["rows_exported"] == 1  # only 1001 is exported
    assert day["payment_ids_exported"] == 1


@pytest.mark.asyncio
async def test_manual_lane_ledger_signed_amounts():
    """_compute_manual_lane correctly computes signed amounts (negative for expense, positive for income)."""
    ledger_rows = _ledger_expense_rows()

    with patch("app.services.event_ledger.get_expense_list", new_callable=AsyncMock, return_value=ledger_rows):
        mock_db = _mock_db_no_batches()
        from app.services.financial_closing import _compute_manual_lane
        days, _, _ = await _compute_manual_lane(
            mock_db, "test-seller", "2026-01-15", "2026-01-15"
        )

    day = days[0]
    # expense: -50.0, income: +30.0 -> total = -20.0
    assert day["amount_total_signed"] == -20.0


# ── extrato_coverage_checker: _lookup_expense_ids ───────────────


def test_lookup_expense_ids_ledger_mode():
    """_lookup_expense_ids queries payment_events with event_type=expense_captured."""
    mock_db = _mock_db_for_expense_lookup("ml_payment_id", [1001, 1003])

    from app.services.extrato_coverage_checker import _lookup_expense_ids
    result = _lookup_expense_ids(mock_db, "test-seller", [1001, 1002, 1003])

    assert result == {1001, 1003}
    mock_db.table.assert_any_call("payment_events")


def test_lookup_expense_ids_ledger_empty():
    """_lookup_expense_ids with no matching IDs returns empty set."""
    mock_db = _mock_db_for_expense_lookup("ml_payment_id", [])

    from app.services.extrato_coverage_checker import _lookup_expense_ids
    result = _lookup_expense_ids(mock_db, "test-seller", [1001, 1002])

    assert result == set()


def test_lookup_expense_ids_ledger_batched():
    """_lookup_expense_ids batches lookups in chunks of 100."""
    # Create 150 source_ids to trigger 2 batches
    source_ids = list(range(1, 151))

    mock_db = MagicMock()
    empty_result = MagicMock()
    empty_result.data = []
    # Chain for ledger: .table().select().eq().eq().in_().execute()
    chain = mock_db.table.return_value.select.return_value.eq.return_value
    chain.eq.return_value.in_.return_value.execute.return_value = empty_result

    from app.services.extrato_coverage_checker import _lookup_expense_ids
    _lookup_expense_ids(mock_db, "test-seller", source_ids)

    # Should have called in_() twice (100 + 50)
    assert chain.eq.return_value.in_.call_count == 2
