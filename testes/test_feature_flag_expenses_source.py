"""
Tests for the expenses_source feature flag in financial_closing.py and extrato_coverage_checker.py.

Verifies that:
- expenses_source='mp_expenses' (default) → original behavior (queries mp_expenses table)
- expenses_source='ledger' → reads from event ledger via get_expense_list / payment_events

Run: python3 -m pytest testes/test_feature_flag_expenses_source.py -v
"""
import pytest
from unittest.mock import patch, AsyncMock, MagicMock


# ── Sample data ──────────────────────────────────────────────────


def _mp_expenses_rows():
    """Sample rows as returned by mp_expenses table query."""
    return [
        {
            "payment_id": 1001,
            "amount": 50.0,
            "expense_direction": "expense",
            "status": "exported",
            "date_created": "2026-01-15T10:00:00.000-03:00",
            "date_approved": "2026-01-15T10:00:00.000-03:00",
        },
        {
            "payment_id": 1002,
            "amount": 30.0,
            "expense_direction": "income",
            "status": "pending_review",
            "date_created": "2026-01-15T11:00:00.000-03:00",
            "date_approved": "2026-01-15T11:00:00.000-03:00",
        },
    ]


def _ledger_expense_rows():
    """Sample rows as returned by get_expense_list (mp_expenses-compatible shape)."""
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


# ── financial_closing: _compute_manual_lane ─────────────────────


def _mock_db_mp_expenses(rows):
    """Build a mock DB that returns rows for mp_expenses queries."""
    mock_db = MagicMock()
    chain = mock_db.table.return_value.select.return_value.eq.return_value
    # date filters (.gte/.lte) return the chain back
    chain.gte.return_value = chain
    chain.lte.return_value = chain
    chain.order.return_value.execute.return_value.data = rows
    # batch_tables check — simulate not available to simplify
    mock_db.table.return_value.select.return_value.limit.return_value.execute.side_effect = Exception("no table")
    return mock_db


def _mock_db_no_batches():
    """DB mock with batch_tables check failing."""
    mock_db = MagicMock()
    # For the batch_tables_available check
    mock_db.table.return_value.select.return_value.limit.return_value.execute.side_effect = Exception("no table")
    return mock_db


@pytest.mark.asyncio
async def test_manual_lane_mp_expenses_default():
    """Default mode (mp_expenses) queries mp_expenses table."""
    rows = _mp_expenses_rows()
    mock_db = _mock_db_mp_expenses(rows)

    with patch("app.services.financial_closing.settings") as mock_settings:
        mock_settings.expenses_source = "mp_expenses"
        from app.services.financial_closing import _compute_manual_lane
        days, missing_ids, import_source = await _compute_manual_lane(
            mock_db, "test-seller", "2026-01-15", "2026-01-15"
        )

    # Should have queried mp_expenses table
    mock_db.table.assert_any_call("mp_expenses")
    assert len(days) == 1
    assert days[0]["date"] == "2026-01-15"
    assert days[0]["rows_total"] == 2


@pytest.mark.asyncio
async def test_manual_lane_ledger_mode():
    """Ledger mode calls get_expense_list instead of mp_expenses table."""
    ledger_rows = _ledger_expense_rows()

    with patch("app.services.financial_closing.settings") as mock_settings, \
         patch("app.services.event_ledger.get_expense_list", new_callable=AsyncMock, return_value=ledger_rows) as mock_gel:
        mock_settings.expenses_source = "ledger"

        mock_db = _mock_db_no_batches()
        from app.services.financial_closing import _compute_manual_lane
        days, missing_ids, import_source = await _compute_manual_lane(
            mock_db, "test-seller", "2026-01-15", "2026-01-15"
        )

    # Should have called get_expense_list, NOT mp_expenses
    mock_gel.assert_called_once_with(
        "test-seller", date_from="2026-01-15", date_to="2026-01-15", limit=1_000_000,
    )
    assert len(days) == 1
    assert days[0]["date"] == "2026-01-15"
    assert days[0]["rows_total"] == 2


@pytest.mark.asyncio
async def test_manual_lane_ledger_exported_status():
    """Ledger mode correctly identifies exported rows via status field."""
    ledger_rows = _ledger_expense_rows()  # row 1001 has status="exported"

    with patch("app.services.financial_closing.settings") as mock_settings, \
         patch("app.services.event_ledger.get_expense_list", new_callable=AsyncMock, return_value=ledger_rows):
        mock_settings.expenses_source = "ledger"

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
    """Ledger mode correctly computes signed amounts (negative for expense, positive for income)."""
    ledger_rows = _ledger_expense_rows()

    with patch("app.services.financial_closing.settings") as mock_settings, \
         patch("app.services.event_ledger.get_expense_list", new_callable=AsyncMock, return_value=ledger_rows):
        mock_settings.expenses_source = "ledger"

        mock_db = _mock_db_no_batches()
        from app.services.financial_closing import _compute_manual_lane
        days, _, _ = await _compute_manual_lane(
            mock_db, "test-seller", "2026-01-15", "2026-01-15"
        )

    day = days[0]
    # expense: -50.0, income: +30.0 → total = -20.0
    assert day["amount_total_signed"] == -20.0


# ── extrato_coverage_checker: _lookup_expense_ids ───────────────


def _mock_db_for_expense_lookup(table_name: str, payment_id_field: str, found_ids: list[int]):
    """Build a mock DB that returns found_ids for a table lookup."""
    mock_db = MagicMock()

    def _make_result(ids):
        result = MagicMock()
        result.data = [{payment_id_field: pid} for pid in ids]
        return result

    # Chain: db.table(X).select(Y).eq().eq().in_().execute()
    chain = mock_db.table.return_value.select.return_value.eq.return_value
    chain.eq.return_value.in_.return_value.execute.return_value = _make_result(found_ids)
    # mp_expenses path: db.table(X).select(Y).eq().in_().execute()
    chain.in_.return_value.execute.return_value = _make_result(found_ids)
    return mock_db


def test_lookup_expense_ids_mp_expenses_default():
    """Default mode queries mp_expenses table."""
    mock_db = _mock_db_for_expense_lookup("mp_expenses", "payment_id", [1001, 1003])

    with patch("app.services.extrato_coverage_checker.settings") as mock_settings:
        mock_settings.expenses_source = "mp_expenses"
        from app.services.extrato_coverage_checker import _lookup_expense_ids
        result = _lookup_expense_ids(mock_db, "test-seller", [1001, 1002, 1003])

    assert result == {1001, 1003}
    mock_db.table.assert_any_call("mp_expenses")


def test_lookup_expense_ids_ledger_mode():
    """Ledger mode queries payment_events with event_type=expense_captured."""
    mock_db = _mock_db_for_expense_lookup("payment_events", "ml_payment_id", [1001, 1003])

    with patch("app.services.extrato_coverage_checker.settings") as mock_settings:
        mock_settings.expenses_source = "ledger"
        from app.services.extrato_coverage_checker import _lookup_expense_ids
        result = _lookup_expense_ids(mock_db, "test-seller", [1001, 1002, 1003])

    assert result == {1001, 1003}
    mock_db.table.assert_any_call("payment_events")


def test_lookup_expense_ids_ledger_empty():
    """Ledger mode with no matching IDs returns empty set."""
    mock_db = _mock_db_for_expense_lookup("payment_events", "ml_payment_id", [])

    with patch("app.services.extrato_coverage_checker.settings") as mock_settings:
        mock_settings.expenses_source = "ledger"
        from app.services.extrato_coverage_checker import _lookup_expense_ids
        result = _lookup_expense_ids(mock_db, "test-seller", [1001, 1002])

    assert result == set()


def test_lookup_expense_ids_mp_expenses_empty():
    """mp_expenses mode with no matching IDs returns empty set."""
    mock_db = _mock_db_for_expense_lookup("mp_expenses", "payment_id", [])

    with patch("app.services.extrato_coverage_checker.settings") as mock_settings:
        mock_settings.expenses_source = "mp_expenses"
        from app.services.extrato_coverage_checker import _lookup_expense_ids
        result = _lookup_expense_ids(mock_db, "test-seller", [1001])

    assert result == set()


def test_lookup_expense_ids_ledger_batched():
    """Ledger mode batches lookups in chunks of 100."""
    # Create 150 source_ids to trigger 2 batches
    source_ids = list(range(1, 151))

    mock_db = MagicMock()
    empty_result = MagicMock()
    empty_result.data = []
    # Chain for ledger: .table().select().eq().eq().in_().execute()
    chain = mock_db.table.return_value.select.return_value.eq.return_value
    chain.eq.return_value.in_.return_value.execute.return_value = empty_result

    with patch("app.services.extrato_coverage_checker.settings") as mock_settings:
        mock_settings.expenses_source = "ledger"
        from app.services.extrato_coverage_checker import _lookup_expense_ids
        _lookup_expense_ids(mock_db, "test-seller", source_ids)

    # Should have called in_() twice (100 + 50)
    assert chain.eq.return_value.in_.call_count == 2
