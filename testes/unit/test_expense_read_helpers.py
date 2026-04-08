"""
Unit tests for event_ledger expense read helpers:
_group_expense_events, _build_expense_row, get_expense_list, get_expense_stats, get_pending_exports.

Pure function tests for _group_expense_events and _build_expense_row.
Async tests mock _fetch_expense_events to avoid DB calls.

Run: python3 -m pytest testes/test_expense_read_helpers.py -v
"""
import pytest
from unittest.mock import patch, AsyncMock

from app.services.event_ledger import (
    _group_expense_events,
    _build_expense_row,
    get_expense_list,
    get_expense_stats,
    get_pending_exports,
)


# ── Sample event data builders ──────────────────────────────────

def _captured_event(ref_id: str, **meta_overrides) -> dict:
    """Build a sample expense_captured event row."""
    meta = {
        "expense_type": "difal",
        "expense_direction": "expense",
        "ca_category": None,
        "auto_categorized": False,
        "description": "Débito por DIFAL",
        "amount": 11.04,
        "date_created": "2026-01-21T10:00:00-03:00",
        "date_approved": "2026-01-21T10:00:00-03:00",
        "business_branch": None,
        "operation_type": None,
        "payment_method": None,
        "external_reference": None,
        "beneficiary_name": None,
        "notes": None,
    }
    meta.update(meta_overrides)
    return {
        "reference_id": ref_id,
        "event_type": "expense_captured",
        "signed_amount": -abs(meta["amount"]),
        "competencia_date": "2026-01-21",
        "metadata": meta,
        "created_at": "2026-01-21T13:00:00Z",
    }


def _classified_event(ref_id: str, ca_category: str = "2.2.7 Simples Nacional") -> dict:
    return {
        "reference_id": ref_id,
        "event_type": "expense_classified",
        "signed_amount": 0,
        "competencia_date": "2026-01-21",
        "metadata": {"expense_type": "difal", "ca_category": ca_category},
        "created_at": "2026-01-21T13:00:01Z",
    }


def _reviewed_event(ref_id: str, **meta_overrides) -> dict:
    meta = {"approved": True, "reviewer": "admin"}
    meta.update(meta_overrides)
    return {
        "reference_id": ref_id,
        "event_type": "expense_reviewed",
        "signed_amount": 0,
        "competencia_date": "2026-01-21",
        "metadata": meta,
        "created_at": "2026-01-21T14:00:00Z",
    }


def _exported_event(ref_id: str, batch_id: str = "exp_abc123") -> dict:
    return {
        "reference_id": ref_id,
        "event_type": "expense_exported",
        "signed_amount": 0,
        "competencia_date": "2026-01-21",
        "metadata": {"expense_type": "difal", "batch_id": batch_id},
        "created_at": "2026-01-21T15:00:00Z",
    }


# ===========================================================================
# _group_expense_events
# ===========================================================================

class TestGroupExpenseEvents:
    def test_single_captured(self):
        events = [_captured_event("12345")]
        grouped = _group_expense_events(events)
        assert "12345" in grouped
        assert grouped["12345"]["event_types"] == {"expense_captured"}
        assert grouped["12345"]["captured"]["amount"] == 11.04

    def test_captured_plus_classified(self):
        events = [_captured_event("12345"), _classified_event("12345")]
        grouped = _group_expense_events(events)
        assert grouped["12345"]["event_types"] == {"expense_captured", "expense_classified"}
        # ca_category merged from classified
        assert grouped["12345"]["captured"]["ca_category"] == "2.2.7 Simples Nacional"

    def test_reviewed_merges_fields(self):
        events = [
            _captured_event("12345"),
            _reviewed_event("12345", ca_category="2.1.1 Custom Category"),
        ]
        grouped = _group_expense_events(events)
        assert grouped["12345"]["captured"]["ca_category"] == "2.1.1 Custom Category"

    def test_reviewed_does_not_merge_none(self):
        """Review metadata with None values should not overwrite captured data."""
        events = [
            _captured_event("12345", description="Original"),
            _reviewed_event("12345"),  # no description key
        ]
        grouped = _group_expense_events(events)
        assert grouped["12345"]["captured"]["description"] == "Original"

    def test_multiple_reference_ids(self):
        events = [_captured_event("111"), _captured_event("222")]
        grouped = _group_expense_events(events)
        assert len(grouped) == 2
        assert "111" in grouped
        assert "222" in grouped

    def test_empty_events(self):
        assert _group_expense_events([]) == {}

    def test_exported_sets_event_type(self):
        events = [_captured_event("12345"), _exported_event("12345")]
        grouped = _group_expense_events(events)
        assert "expense_exported" in grouped["12345"]["event_types"]

    def test_full_lifecycle(self):
        events = [
            _captured_event("12345"),
            _classified_event("12345"),
            _reviewed_event("12345"),
            _exported_event("12345"),
        ]
        grouped = _group_expense_events(events)
        assert grouped["12345"]["event_types"] == {
            "expense_captured", "expense_classified", "expense_reviewed", "expense_exported"
        }


# ===========================================================================
# _build_expense_row
# ===========================================================================

class TestBuildExpenseRow:
    def test_pending_review_status(self):
        group = {
            "captured": {"expense_type": "difal", "expense_direction": "expense", "amount": 11.04,
                         "description": "DIFAL", "date_created": "2026-01-21T10:00:00-03:00",
                         "date_approved": "2026-01-21T10:00:00-03:00"},
            "event_types": {"expense_captured"},
            "created_at": "2026-01-21T13:00:00Z",
        }
        row = _build_expense_row("12345", group)
        assert row["status"] == "pending_review"
        assert row["payment_id"] == "12345"
        assert row["id"] == "12345"
        assert row["expense_type"] == "difal"
        assert row["amount"] == 11.04

    def test_auto_categorized_status(self):
        group = {
            "captured": {"expense_type": "difal", "ca_category": "2.2.7 Simples Nacional",
                         "auto_categorized": True, "amount": 5.0},
            "event_types": {"expense_captured", "expense_classified"},
            "created_at": "2026-01-21T13:00:00Z",
        }
        row = _build_expense_row("55555", group)
        assert row["status"] == "auto_categorized"
        assert row["ca_category"] == "2.2.7 Simples Nacional"
        assert row["auto_categorized"] is True

    def test_exported_status(self):
        group = {
            "captured": {"expense_type": "subscription", "amount": 20.0},
            "event_types": {"expense_captured", "expense_classified", "expense_exported"},
            "created_at": "2026-01-21T13:00:00Z",
        }
        row = _build_expense_row("77777", group)
        assert row["status"] == "exported"

    def test_defaults_for_missing_metadata(self):
        group = {
            "captured": {},
            "event_types": {"expense_captured"},
            "created_at": None,
        }
        row = _build_expense_row("99999", group)
        assert row["expense_type"] == "unknown"
        assert row["expense_direction"] == "expense"
        assert row["auto_categorized"] is False
        assert row["amount"] == 0
        assert row["ca_category"] is None

    def test_all_fields_present(self):
        """Verify all mp_expenses-compatible fields are present."""
        group = {
            "captured": {"expense_type": "difal", "expense_direction": "expense",
                         "amount": 11.0, "description": "test", "ca_category": "2.1.1",
                         "auto_categorized": True, "business_branch": "Sede",
                         "operation_type": "regular_payment", "payment_method": "pix",
                         "external_reference": "REF123", "beneficiary_name": "John",
                         "notes": "nota", "date_created": "2026-01-01",
                         "date_approved": "2026-01-01", "febraban_code": "001"},
            "event_types": {"expense_captured"},
            "created_at": "2026-01-01T00:00:00Z",
        }
        row = _build_expense_row("12345", group)
        expected_keys = {
            "id", "payment_id", "expense_type", "expense_direction", "ca_category",
            "auto_categorized", "amount", "description", "business_branch",
            "operation_type", "payment_method", "external_reference", "febraban_code",
            "date_created", "date_approved", "beneficiary_name", "notes",
            "status", "exported_at", "created_at",
        }
        assert set(row.keys()) == expected_keys


# ===========================================================================
# get_expense_list (async, mocked)
# ===========================================================================

class TestGetExpenseList:
    @pytest.mark.asyncio
    async def test_basic_list(self):
        events = [
            _captured_event("111", amount=10.0, date_created="2026-01-20T10:00:00-03:00"),
            _captured_event("222", amount=20.0, date_created="2026-01-21T10:00:00-03:00"),
        ]
        with patch("app.services.event_ledger._fetch_expense_events", new_callable=AsyncMock, return_value=events):
            rows = await get_expense_list("141air")
        assert len(rows) == 2
        # Sorted by date_created descending
        assert rows[0]["payment_id"] == "222"
        assert rows[1]["payment_id"] == "111"

    @pytest.mark.asyncio
    async def test_filter_by_status(self):
        events = [
            _captured_event("111"),
            _captured_event("222"),
            _classified_event("222"),
        ]
        with patch("app.services.event_ledger._fetch_expense_events", new_callable=AsyncMock, return_value=events):
            rows = await get_expense_list("141air", status="auto_categorized")
        assert len(rows) == 1
        assert rows[0]["payment_id"] == "222"

    @pytest.mark.asyncio
    async def test_filter_by_expense_type(self):
        events = [
            _captured_event("111", expense_type="difal"),
            _captured_event("222", expense_type="subscription"),
        ]
        with patch("app.services.event_ledger._fetch_expense_events", new_callable=AsyncMock, return_value=events):
            rows = await get_expense_list("141air", expense_type="subscription")
        assert len(rows) == 1
        assert rows[0]["expense_type"] == "subscription"

    @pytest.mark.asyncio
    async def test_filter_by_direction(self):
        events = [
            _captured_event("111", expense_direction="expense"),
            _captured_event("222", expense_direction="income"),
        ]
        with patch("app.services.event_ledger._fetch_expense_events", new_callable=AsyncMock, return_value=events):
            rows = await get_expense_list("141air", direction="income")
        assert len(rows) == 1
        assert rows[0]["expense_direction"] == "income"

    @pytest.mark.asyncio
    async def test_pagination(self):
        events = [_captured_event(str(i), date_created=f"2026-01-{20+i:02d}T10:00:00-03:00") for i in range(5)]
        with patch("app.services.event_ledger._fetch_expense_events", new_callable=AsyncMock, return_value=events):
            rows = await get_expense_list("141air", limit=2, offset=1)
        assert len(rows) == 2

    @pytest.mark.asyncio
    async def test_empty_result(self):
        with patch("app.services.event_ledger._fetch_expense_events", new_callable=AsyncMock, return_value=[]):
            rows = await get_expense_list("141air")
        assert rows == []

    @pytest.mark.asyncio
    async def test_skips_non_captured_only(self):
        """Events without expense_captured are skipped."""
        events = [_classified_event("111")]  # no captured
        with patch("app.services.event_ledger._fetch_expense_events", new_callable=AsyncMock, return_value=events):
            rows = await get_expense_list("141air")
        assert rows == []


# ===========================================================================
# get_expense_stats (async, mocked)
# ===========================================================================

class TestGetExpenseStats:
    @pytest.mark.asyncio
    async def test_basic_stats(self):
        events = [
            _captured_event("111", expense_type="difal", expense_direction="expense", amount=10.0),
            _captured_event("222", expense_type="subscription", expense_direction="expense", amount=20.0),
            _classified_event("222"),
        ]
        with patch("app.services.event_ledger._fetch_expense_events", new_callable=AsyncMock, return_value=events):
            stats = await get_expense_stats("141air")

        assert stats["seller"] == "141air"
        assert stats["total"] == 2
        assert stats["total_amount"] == 30.0
        assert stats["by_type"] == {"difal": 1, "subscription": 1}
        assert stats["by_direction"] == {"expense": 2}
        assert stats["by_status"]["pending_review"] == 1
        assert stats["by_status"]["auto_categorized"] == 1
        assert stats["pending_review_count"] == 1
        assert stats["auto_categorized_count"] == 1

    @pytest.mark.asyncio
    async def test_status_filter(self):
        events = [
            _captured_event("111", amount=10.0),
            _captured_event("222", amount=20.0),
            _classified_event("222"),
        ]
        with patch("app.services.event_ledger._fetch_expense_events", new_callable=AsyncMock, return_value=events):
            stats = await get_expense_stats("141air", status_filter=["pending_review"])

        assert stats["total"] == 1
        assert stats["total_amount"] == 10.0

    @pytest.mark.asyncio
    async def test_empty_stats(self):
        with patch("app.services.event_ledger._fetch_expense_events", new_callable=AsyncMock, return_value=[]):
            stats = await get_expense_stats("141air")

        assert stats["total"] == 0
        assert stats["total_amount"] == 0.0
        assert stats["by_type"] == {}
        assert stats["pending_review_count"] == 0
        assert stats["auto_categorized_count"] == 0

    @pytest.mark.asyncio
    async def test_rounding(self):
        events = [
            _captured_event("111", amount=10.333),
            _captured_event("222", amount=20.667),
        ]
        with patch("app.services.event_ledger._fetch_expense_events", new_callable=AsyncMock, return_value=events):
            stats = await get_expense_stats("141air")
        assert stats["total_amount"] == 31.0


# ===========================================================================
# get_pending_exports (async, mocked)
# ===========================================================================

class TestGetPendingExports:
    @pytest.mark.asyncio
    async def test_excludes_exported(self):
        events = [
            _captured_event("111"),
            _captured_event("222"),
            _exported_event("222"),
        ]
        with patch("app.services.event_ledger._fetch_expense_events", new_callable=AsyncMock, return_value=events):
            rows = await get_pending_exports("141air")
        assert len(rows) == 1
        assert rows[0]["payment_id"] == "111"

    @pytest.mark.asyncio
    async def test_includes_classified_not_exported(self):
        events = [
            _captured_event("111"),
            _classified_event("111"),
        ]
        with patch("app.services.event_ledger._fetch_expense_events", new_callable=AsyncMock, return_value=events):
            rows = await get_pending_exports("141air")
        assert len(rows) == 1
        assert rows[0]["status"] == "auto_categorized"

    @pytest.mark.asyncio
    async def test_status_filter(self):
        events = [
            _captured_event("111"),  # pending_review
            _captured_event("222"),
            _classified_event("222"),  # auto_categorized
        ]
        with patch("app.services.event_ledger._fetch_expense_events", new_callable=AsyncMock, return_value=events):
            rows = await get_pending_exports("141air", status_filter=["auto_categorized"])
        assert len(rows) == 1
        assert rows[0]["payment_id"] == "222"

    @pytest.mark.asyncio
    async def test_empty_when_all_exported(self):
        events = [
            _captured_event("111"),
            _exported_event("111"),
        ]
        with patch("app.services.event_ledger._fetch_expense_events", new_callable=AsyncMock, return_value=events):
            rows = await get_pending_exports("141air")
        assert rows == []

    @pytest.mark.asyncio
    async def test_sorted_ascending_by_date(self):
        events = [
            _captured_event("222", date_created="2026-01-22T10:00:00-03:00"),
            _captured_event("111", date_created="2026-01-21T10:00:00-03:00"),
        ]
        with patch("app.services.event_ledger._fetch_expense_events", new_callable=AsyncMock, return_value=events):
            rows = await get_pending_exports("141air")
        assert rows[0]["payment_id"] == "111"
        assert rows[1]["payment_id"] == "222"

    @pytest.mark.asyncio
    async def test_enriched_with_classified_category(self):
        events = [
            _captured_event("111", ca_category=None),
            _classified_event("111", ca_category="2.2.7 Simples Nacional"),
        ]
        with patch("app.services.event_ledger._fetch_expense_events", new_callable=AsyncMock, return_value=events):
            rows = await get_pending_exports("141air")
        assert rows[0]["ca_category"] == "2.2.7 Simples Nacional"

    @pytest.mark.asyncio
    async def test_reviewed_but_not_exported(self):
        """Reviewed expenses are still pending export."""
        events = [
            _captured_event("111"),
            _reviewed_event("111"),
        ]
        with patch("app.services.event_ledger._fetch_expense_events", new_callable=AsyncMock, return_value=events):
            rows = await get_pending_exports("141air")
        assert len(rows) == 1
        assert rows[0]["status"] == "reviewed"

    @pytest.mark.asyncio
    async def test_row_has_all_export_fields(self):
        """Verify pending export rows have all fields needed by _build_xlsx."""
        events = [_captured_event("111")]
        with patch("app.services.event_ledger._fetch_expense_events", new_callable=AsyncMock, return_value=events):
            rows = await get_pending_exports("141air")
        row = rows[0]
        # Fields required by _build_xlsx in export.py
        for field in ("amount", "expense_direction", "ca_category", "description",
                       "payment_id", "date_approved", "date_created",
                       "auto_categorized", "external_reference", "notes"):
            assert field in row, f"Missing field: {field}"
