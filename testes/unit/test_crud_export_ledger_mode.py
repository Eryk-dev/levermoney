"""
Tests for crud.py and export.py expense endpoints (event ledger mode).

Verifies that:
- crud.py list_expenses calls get_expense_list
- crud.py expense_stats calls get_expense_stats
- crud.py review_expense writes expense_reviewed event
- crud.py pending_review_summary uses ledger
- export.py export_expenses uses get_pending_exports and record_expense_event

Run: python3 -m pytest testes/test_crud_export_ledger_mode.py -v
"""
import pytest
from unittest.mock import patch, AsyncMock, MagicMock


# ── Sample data ──────────────────────────────────────────────────


def _ledger_rows():
    """Expense rows as returned by get_expense_list / get_pending_exports."""
    return [
        {
            "id": "1001",
            "payment_id": "1001",
            "amount": 50.0,
            "expense_direction": "expense",
            "expense_type": "difal",
            "ca_category": "2.1.1 DIFAL",
            "auto_categorized": True,
            "description": "DIFAL pagamento",
            "status": "auto_categorized",
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
            "description": "Cashback ML",
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


def _ledger_stats():
    """Stats as returned by get_expense_stats."""
    return {
        "seller": "test-seller",
        "total": 2,
        "total_amount": 80.0,
        "by_type": {"difal": 1, "cashback": 1},
        "by_direction": {"expense": 1, "income": 1},
        "by_status": {"auto_categorized": 1, "pending_review": 1},
        "pending_review_count": 1,
        "auto_categorized_count": 1,
    }


# ── crud.py: list_expenses ─────────────────────────────────────


@pytest.mark.asyncio
async def test_list_expenses_ledger_mode():
    """list_expenses calls get_expense_list."""
    rows = _ledger_rows()

    with patch("app.services.event_ledger.get_expense_list", new_callable=AsyncMock, return_value=rows):
        from app.routers.expenses.crud import list_expenses
        result = await list_expenses(
            seller_slug="test-seller",
            status=None, expense_type=None, direction=None,
            date_from=None, date_to=None, limit=100, offset=0,
        )

    assert result["seller"] == "test-seller"
    assert result["count"] == 2
    assert result["data"] == rows


@pytest.mark.asyncio
async def test_list_expenses_ledger_with_filters():
    """list_expenses passes filters through to get_expense_list."""
    filtered = [_ledger_rows()[1]]  # only pending_review

    with patch("app.services.event_ledger.get_expense_list", new_callable=AsyncMock, return_value=filtered) as mock_gel:
        from app.routers.expenses.crud import list_expenses
        await list_expenses(
            seller_slug="test-seller",
            status="pending_review", expense_type="cashback",
            direction="income", date_from="2026-01-01", date_to="2026-01-31",
            limit=50, offset=10,
        )

    mock_gel.assert_called_once_with(
        seller_slug="test-seller",
        status="pending_review",
        expense_type="cashback",
        direction="income",
        date_from="2026-01-01",
        date_to="2026-01-31",
        limit=50,
        offset=10,
    )


# ── crud.py: expense_stats ─────────────────────────────────────


@pytest.mark.asyncio
async def test_expense_stats_ledger_mode():
    """expense_stats calls get_expense_stats."""
    stats = _ledger_stats()

    with patch("app.services.event_ledger.get_expense_stats", new_callable=AsyncMock, return_value=stats) as mock_ges:
        from app.routers.expenses.crud import expense_stats
        result = await expense_stats(
            seller_slug="test-seller",
            date_from="2026-01-01", date_to="2026-01-31",
            status_filter="pending_review,auto_categorized",
        )

    mock_ges.assert_called_once_with(
        seller_slug="test-seller",
        date_from="2026-01-01",
        date_to="2026-01-31",
        status_filter=["pending_review", "auto_categorized"],
    )
    assert result["total"] == 2
    assert result["pending_review_count"] == 1


@pytest.mark.asyncio
async def test_expense_stats_ledger_no_filter():
    """expense_stats passes None when no status_filter."""
    stats = _ledger_stats()

    with patch("app.services.event_ledger.get_expense_stats", new_callable=AsyncMock, return_value=stats) as mock_ges:
        from app.routers.expenses.crud import expense_stats
        await expense_stats(
            seller_slug="test-seller",
            date_from=None, date_to=None,
            status_filter=None,
        )

    mock_ges.assert_called_once_with(
        seller_slug="test-seller",
        date_from=None,
        date_to=None,
        status_filter=None,
    )


# ── crud.py: review_expense ───────────────────────────────────


@pytest.mark.asyncio
async def test_review_expense_ledger_mode():
    """review_expense writes expense_reviewed event."""
    mock_db = MagicMock()
    mock_db.table.return_value.select.return_value.eq.return_value.eq.return_value.in_.return_value.execute.return_value.data = [
        {
            "event_type": "expense_captured",
            "competencia_date": "2026-01-15",
            "metadata": {"expense_type": "difal"},
        },
    ]

    with patch("app.routers.expenses.crud.get_db", return_value=mock_db), \
         patch("app.services.event_ledger.record_expense_event", new_callable=AsyncMock) as mock_rec:
        from app.routers.expenses.crud import review_expense, ExpenseReviewUpdate
        req = ExpenseReviewUpdate(ca_category="2.1.1 DIFAL", description="Updated desc")
        result = await review_expense(
            seller_slug="test-seller", expense_id=1001, req=req,
        )

    mock_rec.assert_called_once_with(
        seller_slug="test-seller",
        payment_id="1001",
        event_type="expense_reviewed",
        signed_amount=0,
        competencia_date="2026-01-15",
        expense_type="difal",
        metadata={"ca_category": "2.1.1 DIFAL", "description": "Updated desc"},
    )
    assert result["ok"] is True
    assert result["status"] == "reviewed"
    assert result["ca_category"] == "2.1.1 DIFAL"


@pytest.mark.asyncio
async def test_review_expense_ledger_not_found():
    """review_expense returns 404 when expense_captured not found."""
    mock_db = MagicMock()
    mock_db.table.return_value.select.return_value.eq.return_value.eq.return_value.in_.return_value.execute.return_value.data = []

    with patch("app.routers.expenses.crud.get_db", return_value=mock_db):
        from fastapi import HTTPException
        from app.routers.expenses.crud import review_expense, ExpenseReviewUpdate
        req = ExpenseReviewUpdate(ca_category="2.1.1 DIFAL")
        with pytest.raises(HTTPException) as exc_info:
            await review_expense(seller_slug="test-seller", expense_id=9999, req=req)

    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_review_expense_ledger_already_exported():
    """review_expense returns 409 when expense is already exported."""
    mock_db = MagicMock()
    mock_db.table.return_value.select.return_value.eq.return_value.eq.return_value.in_.return_value.execute.return_value.data = [
        {"event_type": "expense_captured", "competencia_date": "2026-01-15", "metadata": {}},
        {"event_type": "expense_exported", "competencia_date": "2026-01-15", "metadata": {}},
    ]

    with patch("app.routers.expenses.crud.get_db", return_value=mock_db):
        from fastapi import HTTPException
        from app.routers.expenses.crud import review_expense, ExpenseReviewUpdate
        req = ExpenseReviewUpdate(ca_category="2.1.1 DIFAL")
        with pytest.raises(HTTPException) as exc_info:
            await review_expense(seller_slug="test-seller", expense_id=1001, req=req)

    assert exc_info.value.status_code == 409


@pytest.mark.asyncio
async def test_review_expense_ledger_no_fields():
    """review_expense returns 400 when no fields to update."""
    mock_db = MagicMock()
    mock_db.table.return_value.select.return_value.eq.return_value.eq.return_value.in_.return_value.execute.return_value.data = [
        {"event_type": "expense_captured", "competencia_date": "2026-01-15", "metadata": {}},
    ]

    with patch("app.routers.expenses.crud.get_db", return_value=mock_db):
        from fastapi import HTTPException
        from app.routers.expenses.crud import review_expense, ExpenseReviewUpdate
        req = ExpenseReviewUpdate()  # all None
        with pytest.raises(HTTPException) as exc_info:
            await review_expense(seller_slug="test-seller", expense_id=1001, req=req)

    assert exc_info.value.status_code == 400


# ── crud.py: pending_review_summary ────────────────────────────


@pytest.mark.asyncio
async def test_pending_review_summary_ledger_mode():
    """pending_review_summary calls get_expense_list with status=pending_review."""
    pending = [_ledger_rows()[1]]  # only the pending_review row

    with patch("app.services.event_ledger.get_expense_list", new_callable=AsyncMock, return_value=pending) as mock_gel:
        from app.routers.expenses.crud import pending_review_summary
        result = await pending_review_summary(
            seller_slug="test-seller", date_from=None, date_to=None,
        )

    mock_gel.assert_called_once_with(
        seller_slug="test-seller",
        status="pending_review",
        date_from=None,
        date_to=None,
        limit=100_000,
        offset=0,
    )
    assert result["total_pending"] == 1
    assert len(result["by_day"]) == 1
    assert result["by_day"][0]["count"] == 1
    assert result["by_day"][0]["amount_total"] == 30.0


# ── export.py: export_expenses ─────────────────────────────────


@pytest.mark.asyncio
async def test_export_ledger_fetches_from_ledger():
    """export_expenses calls get_pending_exports."""
    rows = _ledger_rows()
    seller = {"slug": "test-seller", "dashboard_empresa": "TEST", "ml_user_id": 123}

    with patch("app.routers.expenses.export.settings") as mock_settings, \
         patch("app.services.event_ledger.get_pending_exports", new_callable=AsyncMock, return_value=rows) as mock_gpe, \
         patch("app.routers.expenses.export.get_db") as mock_get_db, \
         patch("app.routers.expenses.export.get_seller_config", return_value=seller), \
         patch("app.routers.expenses.export._batch_tables_available", return_value=False):
        mock_settings.legacy_daily_google_drive_root_folder_id = ""

        from app.routers.expenses.export import export_expenses
        response = await export_expenses(
            seller_slug="test-seller",
            date_from="2026-01-01", date_to="2026-01-31",
            status_filter="pending_review,auto_categorized",
            mark_exported=False, gdrive_backup=False,
        )

    mock_gpe.assert_called_once_with(
        seller_slug="test-seller",
        date_from="2026-01-01",
        date_to="2026-01-31",
        status_filter=["pending_review", "auto_categorized"],
    )
    # Should return a StreamingResponse (ZIP)
    assert hasattr(response, "media_type")
    assert response.media_type == "application/zip"


@pytest.mark.asyncio
async def test_export_ledger_marks_exported_via_events():
    """export_expenses writes expense_exported events when mark_exported=True."""
    rows = _ledger_rows()
    seller = {"slug": "test-seller", "dashboard_empresa": "TEST", "ml_user_id": 123}

    with patch("app.routers.expenses.export.settings") as mock_settings, \
         patch("app.services.event_ledger.get_pending_exports", new_callable=AsyncMock, return_value=rows), \
         patch("app.services.event_ledger.record_expense_event", new_callable=AsyncMock) as mock_rec, \
         patch("app.routers.expenses.export.get_db") as mock_get_db, \
         patch("app.routers.expenses.export.get_seller_config", return_value=seller), \
         patch("app.routers.expenses.export._batch_tables_available", return_value=False):
        mock_settings.legacy_daily_google_drive_root_folder_id = ""

        from app.routers.expenses.export import export_expenses
        await export_expenses(
            seller_slug="test-seller",
            date_from=None, date_to=None,
            status_filter=None,
            mark_exported=True, gdrive_backup=False,
        )

    # Should have called record_expense_event for each row
    assert mock_rec.call_count == 2
    # Verify first call
    call_args = mock_rec.call_args_list[0]
    assert call_args.kwargs["seller_slug"] == "test-seller"
    assert call_args.kwargs["event_type"] == "expense_exported"
    assert call_args.kwargs["signed_amount"] == 0
    assert "batch_id" in call_args.kwargs["metadata"]


@pytest.mark.asyncio
async def test_export_ledger_no_mark_exported():
    """export_expenses without mark_exported does NOT write events."""
    rows = _ledger_rows()
    seller = {"slug": "test-seller", "dashboard_empresa": "TEST", "ml_user_id": 123}

    with patch("app.routers.expenses.export.settings") as mock_settings, \
         patch("app.services.event_ledger.get_pending_exports", new_callable=AsyncMock, return_value=rows), \
         patch("app.services.event_ledger.record_expense_event", new_callable=AsyncMock) as mock_rec, \
         patch("app.routers.expenses.export.get_db") as mock_get_db, \
         patch("app.routers.expenses.export.get_seller_config", return_value=seller), \
         patch("app.routers.expenses.export._batch_tables_available", return_value=False):
        mock_settings.legacy_daily_google_drive_root_folder_id = ""

        from app.routers.expenses.export import export_expenses
        await export_expenses(
            seller_slug="test-seller",
            date_from=None, date_to=None,
            status_filter=None,
            mark_exported=False, gdrive_backup=False,
        )

    mock_rec.assert_not_called()


@pytest.mark.asyncio
async def test_export_ledger_converts_ids_to_int():
    """export_expenses converts string ids to int for batch persistence."""
    rows = _ledger_rows()
    seller = {"slug": "test-seller", "dashboard_empresa": "TEST", "ml_user_id": 123}

    with patch("app.routers.expenses.export.settings") as mock_settings, \
         patch("app.services.event_ledger.get_pending_exports", new_callable=AsyncMock, return_value=rows), \
         patch("app.routers.expenses.export.get_db") as mock_get_db, \
         patch("app.routers.expenses.export.get_seller_config", return_value=seller), \
         patch("app.routers.expenses.export._batch_tables_available", return_value=False):
        mock_settings.legacy_daily_google_drive_root_folder_id = ""

        from app.routers.expenses.export import export_expenses
        await export_expenses(
            seller_slug="test-seller",
            date_from=None, date_to=None,
            status_filter=None,
            mark_exported=False, gdrive_backup=False,
        )

    # Verify ids were converted to int
    assert rows[0]["id"] == 1001
    assert rows[1]["id"] == 1002


@pytest.mark.asyncio
async def test_export_ledger_event_failure_logged_not_raised():
    """export_expenses catches event write failures as warnings."""
    rows = _ledger_rows()
    seller = {"slug": "test-seller", "dashboard_empresa": "TEST", "ml_user_id": 123}

    with patch("app.routers.expenses.export.settings") as mock_settings, \
         patch("app.services.event_ledger.get_pending_exports", new_callable=AsyncMock, return_value=rows), \
         patch("app.services.event_ledger.record_expense_event", new_callable=AsyncMock, side_effect=Exception("DB error")), \
         patch("app.routers.expenses.export.get_db") as mock_get_db, \
         patch("app.routers.expenses.export.get_seller_config", return_value=seller), \
         patch("app.routers.expenses.export._batch_tables_available", return_value=False):
        mock_settings.legacy_daily_google_drive_root_folder_id = ""

        from app.routers.expenses.export import export_expenses
        # Should NOT raise even though record_expense_event fails
        response = await export_expenses(
            seller_slug="test-seller",
            date_from=None, date_to=None,
            status_filter=None,
            mark_exported=True, gdrive_backup=False,
        )

    assert response.media_type == "application/zip"
