"""
Unit tests for GET /admin/extrato/sellers-status endpoint (US-006).

Tests the endpoint logic: seller filtering, months_needed computation,
months_uploaded aggregation, coverage_status derivation.

All DB calls are mocked — no external dependencies.

Run: python3 -m pytest testes/test_extrato_sellers_status.py -v
"""
from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from app.routers.admin.extrato import extrato_sellers_status


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_db(sellers: list[dict], uploads: list[dict]) -> MagicMock:
    """Build a mock Supabase client that returns sellers and uploads."""
    db = MagicMock()

    sellers_chain = MagicMock()
    sellers_chain.select.return_value = sellers_chain
    sellers_chain.eq.return_value = sellers_chain
    sellers_resp = MagicMock()
    sellers_resp.data = sellers
    sellers_chain.execute.return_value = sellers_resp

    uploads_chain = MagicMock()
    uploads_chain.select.return_value = uploads_chain
    uploads_chain.eq.return_value = uploads_chain
    uploads_resp = MagicMock()
    uploads_resp.data = uploads
    uploads_chain.execute.return_value = uploads_resp

    call_count = 0
    def table_dispatch(name):
        nonlocal call_count
        call_count += 1
        if name == "sellers":
            return sellers_chain
        elif name == "extrato_uploads":
            return uploads_chain
        return MagicMock()

    db.table = table_dispatch
    return db


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@patch("app.routers.admin.extrato.get_db")
async def test_no_sellers_returns_empty(mock_get_db):
    mock_get_db.return_value = _mock_db(sellers=[], uploads=[])
    result = await extrato_sellers_status()
    assert result == []


@pytest.mark.asyncio
@patch("app.routers.admin.extrato.datetime")
@patch("app.routers.admin.extrato.get_db")
async def test_seller_without_ca_start_date(mock_get_db, mock_dt):
    """Seller without ca_start_date should have coverage_status='missing' and empty months."""
    from datetime import datetime, timezone, timedelta
    mock_dt.now.return_value = datetime(2026, 3, 15, 12, 0, tzinfo=timezone(timedelta(hours=-3)))
    mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)

    sellers = [{
        "slug": "test-seller",
        "name": "Test Seller",
        "dashboard_empresa": "TEST",
        "ca_start_date": None,
        "extrato_missing": False,
        "extrato_uploaded_at": None,
        "integration_mode": "dashboard_ca",
        "active": True,
    }]
    mock_get_db.return_value = _mock_db(sellers=sellers, uploads=[])

    result = await extrato_sellers_status()
    assert len(result) == 1
    assert result[0]["slug"] == "test-seller"
    assert result[0]["coverage_status"] == "missing"
    assert result[0]["months_needed"] == []
    assert result[0]["months_uploaded"] == []
    assert result[0]["months_missing"] == []


@pytest.mark.asyncio
@patch("app.routers.admin.extrato.datetime")
@patch("app.routers.admin.extrato.get_db")
async def test_seller_with_full_coverage(mock_get_db, mock_dt):
    """Seller with all months uploaded should have coverage_status='complete'."""
    from datetime import datetime, timezone, timedelta
    mock_dt.now.return_value = datetime(2026, 3, 15, 12, 0, tzinfo=timezone(timedelta(hours=-3)))
    mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)

    sellers = [{
        "slug": "full-seller",
        "name": "Full Seller",
        "dashboard_empresa": "FULL",
        "ca_start_date": "2026-01-01",
        "extrato_missing": False,
        "extrato_uploaded_at": "2026-03-10T12:00:00-03:00",
        "integration_mode": "dashboard_ca",
        "active": True,
    }]
    uploads = [
        {"seller_slug": "full-seller", "month": "2026-01"},
        {"seller_slug": "full-seller", "month": "2026-02"},
        {"seller_slug": "full-seller", "month": "2026-03"},
    ]
    mock_get_db.return_value = _mock_db(sellers=sellers, uploads=uploads)

    result = await extrato_sellers_status()
    assert len(result) == 1
    r = result[0]
    assert r["slug"] == "full-seller"
    assert r["coverage_status"] == "complete"
    assert r["months_needed"] == ["2026-01", "2026-02", "2026-03"]
    assert r["months_uploaded"] == ["2026-01", "2026-02", "2026-03"]
    assert r["months_missing"] == []


@pytest.mark.asyncio
@patch("app.routers.admin.extrato.datetime")
@patch("app.routers.admin.extrato.get_db")
async def test_seller_with_partial_coverage(mock_get_db, mock_dt):
    """Seller missing some months should have coverage_status='partial'."""
    from datetime import datetime, timezone, timedelta
    mock_dt.now.return_value = datetime(2026, 3, 15, 12, 0, tzinfo=timezone(timedelta(hours=-3)))
    mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)

    sellers = [{
        "slug": "partial-seller",
        "name": "Partial Seller",
        "dashboard_empresa": "PARTIAL",
        "ca_start_date": "2026-01-01",
        "extrato_missing": True,
        "extrato_uploaded_at": None,
        "integration_mode": "dashboard_ca",
        "active": True,
    }]
    uploads = [
        {"seller_slug": "partial-seller", "month": "2026-01"},
        # Missing 2026-02 and 2026-03
    ]
    mock_get_db.return_value = _mock_db(sellers=sellers, uploads=uploads)

    result = await extrato_sellers_status()
    assert len(result) == 1
    r = result[0]
    assert r["coverage_status"] == "partial"
    assert r["months_needed"] == ["2026-01", "2026-02", "2026-03"]
    assert r["months_uploaded"] == ["2026-01"]
    assert r["months_missing"] == ["2026-02", "2026-03"]
    assert r["extrato_missing"] is True


@pytest.mark.asyncio
@patch("app.routers.admin.extrato.datetime")
@patch("app.routers.admin.extrato.get_db")
async def test_seller_with_no_uploads(mock_get_db, mock_dt):
    """Seller with zero uploads should have coverage_status='missing'."""
    from datetime import datetime, timezone, timedelta
    mock_dt.now.return_value = datetime(2026, 3, 15, 12, 0, tzinfo=timezone(timedelta(hours=-3)))
    mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)

    sellers = [{
        "slug": "no-upload-seller",
        "name": "No Upload Seller",
        "dashboard_empresa": "NOUP",
        "ca_start_date": "2026-02-01",
        "extrato_missing": False,
        "extrato_uploaded_at": None,
        "integration_mode": "dashboard_ca",
        "active": True,
    }]
    mock_get_db.return_value = _mock_db(sellers=sellers, uploads=[])

    result = await extrato_sellers_status()
    assert len(result) == 1
    r = result[0]
    assert r["coverage_status"] == "missing"
    assert r["months_needed"] == ["2026-02", "2026-03"]
    assert r["months_uploaded"] == []
    assert r["months_missing"] == ["2026-02", "2026-03"]


@pytest.mark.asyncio
@patch("app.routers.admin.extrato.datetime")
@patch("app.routers.admin.extrato.get_db")
async def test_multiple_sellers(mock_get_db, mock_dt):
    """Multiple sellers with different coverage levels."""
    from datetime import datetime, timezone, timedelta
    mock_dt.now.return_value = datetime(2026, 3, 15, 12, 0, tzinfo=timezone(timedelta(hours=-3)))
    mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)

    sellers = [
        {
            "slug": "seller-a",
            "name": "Seller A",
            "dashboard_empresa": "A",
            "ca_start_date": "2026-03-01",
            "extrato_missing": False,
            "extrato_uploaded_at": "2026-03-15T00:00:00",
            "integration_mode": "dashboard_ca",
            "active": True,
        },
        {
            "slug": "seller-b",
            "name": "Seller B",
            "dashboard_empresa": "B",
            "ca_start_date": "2026-01-15",
            "extrato_missing": True,
            "extrato_uploaded_at": None,
            "integration_mode": "dashboard_ca",
            "active": True,
        },
    ]
    uploads = [
        {"seller_slug": "seller-a", "month": "2026-03"},
    ]
    mock_get_db.return_value = _mock_db(sellers=sellers, uploads=uploads)

    result = await extrato_sellers_status()
    assert len(result) == 2

    by_slug = {r["slug"]: r for r in result}
    assert by_slug["seller-a"]["coverage_status"] == "complete"
    assert by_slug["seller-a"]["months_needed"] == ["2026-03"]
    assert by_slug["seller-b"]["coverage_status"] == "missing"
    assert by_slug["seller-b"]["months_needed"] == ["2026-01", "2026-02", "2026-03"]


@pytest.mark.asyncio
@patch("app.routers.admin.extrato.datetime")
@patch("app.routers.admin.extrato.get_db")
async def test_ca_start_date_as_date_object(mock_get_db, mock_dt):
    """ca_start_date may come as a date object from Supabase — should normalize."""
    from datetime import datetime, timezone, timedelta
    mock_dt.now.return_value = datetime(2026, 3, 15, 12, 0, tzinfo=timezone(timedelta(hours=-3)))
    mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)

    sellers = [{
        "slug": "date-obj-seller",
        "name": "Date Obj Seller",
        "dashboard_empresa": "DATEOBJ",
        "ca_start_date": date(2026, 3, 1),  # date object, not string
        "extrato_missing": False,
        "extrato_uploaded_at": None,
        "integration_mode": "dashboard_ca",
        "active": True,
    }]
    mock_get_db.return_value = _mock_db(sellers=sellers, uploads=[])

    result = await extrato_sellers_status()
    assert len(result) == 1
    assert result[0]["ca_start_date"] == "2026-03-01"
    assert result[0]["months_needed"] == ["2026-03"]
