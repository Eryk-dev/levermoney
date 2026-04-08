"""
Unit tests for daily_sync.py — sync window computation, date parsing,
dedup, filtering, and status change detection.

Run: python3 -m pytest testes/test_daily_sync_unit.py -v
"""
import pytest
from unittest.mock import patch, AsyncMock, MagicMock, PropertyMock
from types import SimpleNamespace
from datetime import datetime, date, timedelta, timezone

from app.services.daily_sync import (
    _compute_sync_window,
    _parse_date_yyyy_mm_dd,
    sync_seller_payments,
)


BRT = timezone(timedelta(hours=-3))


def _resp(data=None, count=None):
    return SimpleNamespace(data=data or [], count=count)


# ===========================================================================
# _parse_date_yyyy_mm_dd (pure function)
# ===========================================================================

class TestParseDateYyyyMmDd:

    def test_valid_date(self):
        assert _parse_date_yyyy_mm_dd("2026-01-15") == date(2026, 1, 15)

    def test_valid_date_with_time(self):
        assert _parse_date_yyyy_mm_dd("2026-01-15T10:00:00") == date(2026, 1, 15)

    def test_none_returns_none(self):
        assert _parse_date_yyyy_mm_dd(None) is None

    def test_empty_returns_none(self):
        assert _parse_date_yyyy_mm_dd("") is None

    def test_invalid_returns_none(self):
        assert _parse_date_yyyy_mm_dd("not-a-date") is None


# ===========================================================================
# _compute_sync_window (pure function)
# ===========================================================================

class TestComputeSyncWindow:

    def test_basic_lookback(self):
        """No cursor → lookback from yesterday."""
        now_brt = datetime(2026, 1, 20, 10, 0, tzinfo=BRT)
        begin, end, source = _compute_sync_window(now_brt, lookback_days=3, cursor_state=None)

        assert end == "2026-01-19"  # yesterday
        assert begin == "2026-01-17"  # 3 days back
        assert source == "lookback"

    def test_with_cursor_extends_window(self):
        """Cursor present, cursor_begin < lookback_begin → extends window."""
        now_brt = datetime(2026, 1, 20, 10, 0, tzinfo=BRT)
        cursor = {"last_end_date": "2026-01-14"}  # overlap pushes begin to Jan 13

        begin, end, source = _compute_sync_window(now_brt, lookback_days=3, cursor_state=cursor)

        assert end == "2026-01-19"
        assert begin == "2026-01-13"  # cursor_end(14) - overlap(1) = 13
        assert source == "cursor+lookback"

    def test_cursor_within_lookback(self):
        """Cursor begin > lookback begin → uses lookback (wider window)."""
        now_brt = datetime(2026, 1, 20, 10, 0, tzinfo=BRT)
        cursor = {"last_end_date": "2026-01-19"}  # overlap: Jan 18 > Jan 17

        begin, end, source = _compute_sync_window(now_brt, lookback_days=3, cursor_state=cursor)

        assert end == "2026-01-19"
        # lookback: Jan 17, cursor_begin: Jan 18 → lookback wins (earlier)
        assert begin == "2026-01-17"
        assert source == "lookback"

    def test_begin_clamped_to_end(self):
        """If lookback produces begin > end, clamp begin to end."""
        now_brt = datetime(2026, 1, 1, 0, 30, tzinfo=BRT)  # very early
        begin, end, source = _compute_sync_window(now_brt, lookback_days=0, cursor_state=None)

        # lookback_days=0 → begin = Jan 1, end = Dec 31 → begin > end... Actually:
        # end = now - 1 day = Dec 31, begin = now - 0 days = Jan 1
        # So begin > end → clamped
        assert begin == end

    def test_cursor_with_missing_last_end_date(self):
        """Cursor state without last_end_date → treated as no cursor."""
        now_brt = datetime(2026, 1, 20, 10, 0, tzinfo=BRT)
        cursor = {"last_success_at": "2026-01-19T10:00:00"}  # no last_end_date

        begin, end, source = _compute_sync_window(now_brt, lookback_days=3, cursor_state=cursor)

        assert source == "lookback"
        assert begin == "2026-01-17"


# ===========================================================================
# sync_seller_payments — filtering, dedup, status change detection
# ===========================================================================

class TestSyncSellerPayments:

    def _make_ml_payment(self, pid, status="approved", order_id="2000001",
                         status_detail="accredited", description=None,
                         collector_id=None, date_approved="2026-01-15T10:00:00.000-04:00"):
        payment = {
            "id": pid,
            "status": status,
            "status_detail": status_detail,
            "order": {"id": order_id} if order_id else None,
            "operation_type": "regular_payment",
            "date_approved": date_approved,
            "date_last_updated": date_approved,
            "date_created": date_approved,
        }
        if description:
            payment["description"] = description
        if collector_id is not None:
            payment["collector"] = {"id": collector_id}
        return payment

    @pytest.fixture
    def sync_mocks(self):
        with patch("app.services.daily_sync.get_db") as mock_get_db, \
             patch("app.services.daily_sync.ml_api") as mock_ml, \
             patch("app.services.daily_sync.process_payment_webhook") as mock_proc, \
             patch("app.services.daily_sync.classify_non_order_payment") as mock_classify, \
             patch("app.services.daily_sync.get_all_active_sellers") as mock_sellers, \
             patch("app.services.daily_sync.settings") as mock_settings:

            mock_db = MagicMock()
            mock_get_db.return_value = mock_db
            mock_settings.daily_sync_non_order_mode = "classifier"

            # Default: no existing events
            mock_db.table.return_value.select.return_value.eq.return_value.in_.return_value.range.return_value.execute.return_value = _resp([])

            mock_ml.search_payments = AsyncMock(return_value={"results": [], "paging": {"total": 0}})
            mock_proc.side_effect = AsyncMock()
            mock_classify.side_effect = AsyncMock(return_value=True)

            yield {
                "get_db": mock_get_db,
                "db": mock_db,
                "ml_api": mock_ml,
                "process_payment_webhook": mock_proc,
                "classify_non_order_payment": mock_classify,
                "settings": mock_settings,
            }

    @pytest.mark.asyncio
    async def test_dedup_approved_and_updated(self, sync_mocks):
        """Payments from both date_approved and date_last_updated are deduplicated."""
        m = sync_mocks
        p1 = self._make_ml_payment(100)
        p2 = self._make_ml_payment(200)
        p3 = self._make_ml_payment(100, status="refunded", status_detail="bpp_refunded")  # dupe of p1

        # First call = by_approved, second call = by_updated
        m["ml_api"].search_payments = AsyncMock(side_effect=[
            {"results": [p1, p2], "paging": {"total": 2}},
            {"results": [p3], "paging": {"total": 1}},
        ])

        result = await sync_seller_payments("141air", "2026-01-15", "2026-01-15")

        assert result["total_fetched"] == 2  # deduped: 100 and 200

    @pytest.mark.asyncio
    async def test_skips_cancelled_rejected(self, sync_mocks):
        """Cancelled and rejected payments are skipped."""
        m = sync_mocks
        m["ml_api"].search_payments = AsyncMock(side_effect=[
            {"results": [
                self._make_ml_payment(100, status="cancelled"),
                self._make_ml_payment(200, status="rejected"),
            ], "paging": {"total": 2}},
            {"results": [], "paging": {"total": 0}},
        ])

        result = await sync_seller_payments("141air", "2026-01-15", "2026-01-15")

        assert result["skipped"] == 2
        assert result["orders_processed"] == 0

    @pytest.mark.asyncio
    async def test_skips_marketplace_shipment(self, sync_mocks):
        """marketplace_shipment payments are skipped."""
        m = sync_mocks
        m["ml_api"].search_payments = AsyncMock(side_effect=[
            {"results": [
                self._make_ml_payment(100, description="marketplace_shipment"),
            ], "paging": {"total": 1}},
            {"results": [], "paging": {"total": 0}},
        ])

        result = await sync_seller_payments("141air", "2026-01-15", "2026-01-15")

        assert result["skipped"] == 1
        m["process_payment_webhook"].assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_collector_id(self, sync_mocks):
        """Payments with collector_id (purchases) are skipped."""
        m = sync_mocks
        m["ml_api"].search_payments = AsyncMock(side_effect=[
            {"results": [
                self._make_ml_payment(100, collector_id=999),
            ], "paging": {"total": 1}},
            {"results": [], "paging": {"total": 0}},
        ])

        result = await sync_seller_payments("141air", "2026-01-15", "2026-01-15")

        assert result["skipped"] == 1
        m["process_payment_webhook"].assert_not_called()

    @pytest.mark.asyncio
    async def test_processes_new_order_payment(self, sync_mocks):
        """New approved order payment → process_payment_webhook called."""
        m = sync_mocks
        m["ml_api"].search_payments = AsyncMock(side_effect=[
            {"results": [self._make_ml_payment(100)], "paging": {"total": 1}},
            {"results": [], "paging": {"total": 0}},
        ])

        result = await sync_seller_payments("141air", "2026-01-15", "2026-01-15")

        assert result["orders_processed"] == 1
        m["process_payment_webhook"].assert_called_once()

    @pytest.mark.asyncio
    async def test_status_change_triggers_reprocess(self, sync_mocks):
        """Payment with changed status in ML → should be reprocessed."""
        m = sync_mocks
        payment = self._make_ml_payment(100, status="refunded", status_detail="bpp_refunded")

        m["ml_api"].search_payments = AsyncMock(side_effect=[
            {"results": [payment], "paging": {"total": 1}},
            {"results": [], "paging": {"total": 0}},
        ])

        # Existing event: sale_approved with ml_status="approved"
        existing_event = {
            "ml_payment_id": 100,
            "event_type": "sale_approved",
            "metadata": {"ml_status": "approved", "status_detail": "accredited"},
        }
        m["db"].table.return_value.select.return_value.eq.return_value.in_.return_value.range.return_value.execute.return_value = _resp([existing_event])

        result = await sync_seller_payments("141air", "2026-01-15", "2026-01-15")

        assert result["orders_processed"] == 1
        assert result["orders_reprocessed_updates"] == 1

    @pytest.mark.asyncio
    async def test_already_synced_skips(self, sync_mocks):
        """Payment with same status and synced → skipped."""
        m = sync_mocks
        payment = self._make_ml_payment(100, status="approved", status_detail="accredited")

        m["ml_api"].search_payments = AsyncMock(side_effect=[
            {"results": [payment], "paging": {"total": 1}},
            {"results": [], "paging": {"total": 0}},
        ])

        # Existing events: sale_approved (same status) + ca_sync_completed
        existing_events = [
            {
                "ml_payment_id": 100,
                "event_type": "sale_approved",
                "metadata": {"ml_status": "approved", "status_detail": "accredited"},
            },
            {
                "ml_payment_id": 100,
                "event_type": "ca_sync_completed",
                "metadata": None,
            },
        ]
        m["db"].table.return_value.select.return_value.eq.return_value.in_.return_value.range.return_value.execute.return_value = _resp(existing_events)

        result = await sync_seller_payments("141air", "2026-01-15", "2026-01-15")

        assert result["skipped"] == 1
        m["process_payment_webhook"].assert_not_called()

    @pytest.mark.asyncio
    async def test_non_order_classifier_mode(self, sync_mocks):
        """Non-order approved payment in classifier mode → classify_non_order_payment."""
        m = sync_mocks
        payment = self._make_ml_payment(100, order_id=None, status="approved")

        m["ml_api"].search_payments = AsyncMock(side_effect=[
            {"results": [payment], "paging": {"total": 1}},
            {"results": [], "paging": {"total": 0}},
        ])

        result = await sync_seller_payments("141air", "2026-01-15", "2026-01-15")

        assert result["expenses_classified"] == 1
        m["classify_non_order_payment"].assert_called_once()

    @pytest.mark.asyncio
    async def test_non_order_legacy_mode_defers(self, sync_mocks):
        """Non-order payment in legacy mode → deferred (skipped)."""
        m = sync_mocks
        m["settings"].daily_sync_non_order_mode = "legacy"
        payment = self._make_ml_payment(100, order_id=None, status="approved")

        m["ml_api"].search_payments = AsyncMock(side_effect=[
            {"results": [payment], "paging": {"total": 1}},
            {"results": [], "paging": {"total": 0}},
        ])

        result = await sync_seller_payments("141air", "2026-01-15", "2026-01-15")

        assert result["non_orders_deferred_to_legacy"] == 1
        m["classify_non_order_payment"].assert_not_called()

    @pytest.mark.asyncio
    async def test_queued_status_triggers_reprocess(self, sync_mocks):
        """Payment with queued status (sale_approved but no sync) → should be reprocessed."""
        m = sync_mocks
        payment = self._make_ml_payment(100, status="approved", status_detail="accredited")

        m["ml_api"].search_payments = AsyncMock(side_effect=[
            {"results": [payment], "paging": {"total": 1}},
            {"results": [], "paging": {"total": 0}},
        ])

        # Existing: sale_approved with same status (queued, no ca_sync_completed)
        existing_event = {
            "ml_payment_id": 100,
            "event_type": "sale_approved",
            "metadata": {"ml_status": "approved", "status_detail": "accredited"},
        }
        m["db"].table.return_value.select.return_value.eq.return_value.in_.return_value.range.return_value.execute.return_value = _resp([existing_event])

        result = await sync_seller_payments("141air", "2026-01-15", "2026-01-15")

        # Status is "queued" → should_reprocess = True
        assert result["orders_processed"] == 1
