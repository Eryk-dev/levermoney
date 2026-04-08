"""
Integration tests for event_ledger.py async functions with mocked Supabase.

Tests record_event, get_events, get_balance, get_dre_summary,
get_processed_payment_ids (pagination boundary), get_payment_statuses, etc.

Run: python3 -m pytest testes/test_event_ledger_integration.py -v
"""
import pytest
from unittest.mock import patch, MagicMock
from types import SimpleNamespace

from app.services.event_ledger import (
    record_event,
    get_events,
    get_balance,
    get_dre_summary,
    get_processed_payment_ids,
    get_processed_payment_ids_in,
    get_payment_fees_from_events,
    get_payment_statuses,
    EventRecordError,
)


def _resp(data=None, count=None):
    return SimpleNamespace(data=data or [], count=count)


# ===========================================================================
# record_event
# ===========================================================================

class TestRecordEventAsync:

    @pytest.mark.asyncio
    async def test_success_returns_inserted_row(self):
        inserted = {"id": 1, "event_type": "sale_approved", "signed_amount": 100.0}
        mock_db = MagicMock()
        mock_db.table.return_value.upsert.return_value.execute.return_value = _resp([inserted])

        with patch("app.services.event_ledger.get_db", return_value=mock_db):
            result = await record_event(
                seller_slug="141air", ml_payment_id=12345,
                event_type="sale_approved", signed_amount=100.0,
                competencia_date="2026-01-01", event_date="2026-01-01",
            )

        assert result == inserted
        mock_db.table.assert_called_with("payment_events")

    @pytest.mark.asyncio
    async def test_idempotent_skip_returns_none(self):
        mock_db = MagicMock()
        mock_db.table.return_value.upsert.return_value.execute.return_value = _resp([])

        with patch("app.services.event_ledger.get_db", return_value=mock_db):
            result = await record_event(
                seller_slug="141air", ml_payment_id=12345,
                event_type="sale_approved", signed_amount=100.0,
                competencia_date="2026-01-01", event_date="2026-01-01",
            )

        assert result is None

    @pytest.mark.asyncio
    async def test_db_error_raises_event_record_error(self):
        mock_db = MagicMock()
        mock_db.table.return_value.upsert.return_value.execute.side_effect = Exception("connection refused")

        with patch("app.services.event_ledger.get_db", return_value=mock_db):
            with pytest.raises(EventRecordError, match="connection refused"):
                await record_event(
                    seller_slug="141air", ml_payment_id=12345,
                    event_type="sale_approved", signed_amount=100.0,
                    competencia_date="2026-01-01", event_date="2026-01-01",
                )

    @pytest.mark.asyncio
    async def test_validation_runs_before_db_call(self):
        """Invalid event type should raise ValueError without touching DB."""
        mock_db = MagicMock()
        with patch("app.services.event_ledger.get_db", return_value=mock_db):
            with pytest.raises(ValueError, match="Unknown event_type"):
                await record_event(
                    seller_slug="141air", ml_payment_id=12345,
                    event_type="INVALID", signed_amount=0,
                    competencia_date="2026-01-01", event_date="2026-01-01",
                )
        mock_db.table.assert_not_called()

    @pytest.mark.asyncio
    async def test_custom_idempotency_key(self):
        mock_db = MagicMock()
        mock_db.table.return_value.upsert.return_value.execute.return_value = _resp([{"id": 1}])

        with patch("app.services.event_ledger.get_db", return_value=mock_db):
            await record_event(
                seller_slug="141air", ml_payment_id=12345,
                event_type="partial_refund", signed_amount=-50.0,
                competencia_date="2026-01-01", event_date="2026-01-01",
                idempotency_key="141air:12345:partial_refund:0",
            )

        upsert_call = mock_db.table.return_value.upsert.call_args
        row = upsert_call[0][0]
        assert row["idempotency_key"] == "141air:12345:partial_refund:0"

    @pytest.mark.asyncio
    async def test_default_idempotency_key_generated(self):
        mock_db = MagicMock()
        mock_db.table.return_value.upsert.return_value.execute.return_value = _resp([{"id": 1}])

        with patch("app.services.event_ledger.get_db", return_value=mock_db):
            await record_event(
                seller_slug="141air", ml_payment_id=12345,
                event_type="fee_charged", signed_amount=-20.0,
                competencia_date="2026-01-01", event_date="2026-01-01",
            )

        row = mock_db.table.return_value.upsert.call_args[0][0]
        assert row["idempotency_key"] == "141air:12345:fee_charged"


# ===========================================================================
# get_events
# ===========================================================================

class TestGetEventsAsync:

    @pytest.mark.asyncio
    async def test_returns_events(self):
        events = [
            {"event_type": "sale_approved", "signed_amount": 100.0},
            {"event_type": "fee_charged", "signed_amount": -15.0},
        ]
        mock_db = MagicMock()
        chain = mock_db.table.return_value.select.return_value
        chain.eq.return_value.eq.return_value.order.return_value.execute.return_value = _resp(events)

        with patch("app.services.event_ledger.get_db", return_value=mock_db):
            result = await get_events("141air", 12345)

        assert len(result) == 2
        assert result[0]["event_type"] == "sale_approved"

    @pytest.mark.asyncio
    async def test_empty_returns_empty_list(self):
        mock_db = MagicMock()
        chain = mock_db.table.return_value.select.return_value
        chain.eq.return_value.eq.return_value.order.return_value.execute.return_value = _resp(None)

        with patch("app.services.event_ledger.get_db", return_value=mock_db):
            result = await get_events("141air", 99999)

        assert result == []


# ===========================================================================
# get_balance
# ===========================================================================

class TestGetBalanceAsync:

    @pytest.mark.asyncio
    async def test_computes_sum(self):
        rows = [
            {"signed_amount": 100.0},
            {"signed_amount": -15.0},
            {"signed_amount": -5.0},
        ]
        mock_db = MagicMock()
        chain = mock_db.table.return_value.select.return_value
        chain.eq.return_value.eq.return_value.execute.return_value = _resp(rows)

        with patch("app.services.event_ledger.get_db", return_value=mock_db):
            balance = await get_balance("141air", 12345)

        assert balance == 80.0

    @pytest.mark.asyncio
    async def test_with_as_of_date(self):
        rows = [{"signed_amount": 100.0}]
        mock_db = MagicMock()
        chain = mock_db.table.return_value.select.return_value
        chain.eq.return_value.eq.return_value.lte.return_value.execute.return_value = _resp(rows)

        with patch("app.services.event_ledger.get_db", return_value=mock_db):
            balance = await get_balance("141air", 12345, as_of_date="2026-01-15")

        assert balance == 100.0

    @pytest.mark.asyncio
    async def test_empty_returns_zero(self):
        mock_db = MagicMock()
        chain = mock_db.table.return_value.select.return_value
        chain.eq.return_value.eq.return_value.execute.return_value = _resp(None)

        with patch("app.services.event_ledger.get_db", return_value=mock_db):
            balance = await get_balance("141air", 99999)

        assert balance == 0.0


# ===========================================================================
# get_dre_summary
# ===========================================================================

class TestGetDreSummaryAsync:

    @pytest.mark.asyncio
    async def test_aggregates_by_event_type(self):
        rows = [
            {"event_type": "sale_approved", "signed_amount": 100.0},
            {"event_type": "sale_approved", "signed_amount": 200.0},
            {"event_type": "fee_charged", "signed_amount": -30.0},
        ]
        mock_db = MagicMock()
        chain = mock_db.table.return_value.select.return_value
        chain.eq.return_value.not_.like.return_value.not_.like.return_value.gte.return_value.lte.return_value.range.return_value.execute.return_value = _resp(rows)

        with patch("app.services.event_ledger.get_db", return_value=mock_db):
            summary = await get_dre_summary("141air", "2026-01-01", "2026-01-31")

        assert summary["sale_approved"] == 300.0
        assert summary["fee_charged"] == -30.0

    @pytest.mark.asyncio
    async def test_pagination(self):
        """When first page returns exactly page_limit rows, fetches next page."""
        page1 = [{"event_type": "sale_approved", "signed_amount": 1.0}] * 1000
        page2 = [{"event_type": "fee_charged", "signed_amount": -0.5}] * 3

        mock_db = MagicMock()
        chain = mock_db.table.return_value.select.return_value
        execute_mock = chain.eq.return_value.not_.like.return_value.not_.like.return_value.gte.return_value.lte.return_value.range.return_value.execute
        execute_mock.side_effect = [_resp(page1), _resp(page2)]

        with patch("app.services.event_ledger.get_db", return_value=mock_db):
            summary = await get_dre_summary("141air", "2026-01-01", "2026-01-31")

        assert summary["sale_approved"] == 1000.0
        assert summary["fee_charged"] == -1.5

    @pytest.mark.asyncio
    async def test_empty(self):
        mock_db = MagicMock()
        chain = mock_db.table.return_value.select.return_value
        chain.eq.return_value.not_.like.return_value.not_.like.return_value.gte.return_value.lte.return_value.range.return_value.execute.return_value = _resp([])

        with patch("app.services.event_ledger.get_db", return_value=mock_db):
            summary = await get_dre_summary("141air", "2026-01-01", "2026-01-31")

        assert summary == {}


# ===========================================================================
# get_processed_payment_ids (pagination boundary)
# ===========================================================================

class TestGetProcessedPaymentIdsAsync:

    @pytest.mark.asyncio
    async def test_basic(self):
        rows = [{"ml_payment_id": 100}, {"ml_payment_id": 200}]
        mock_db = MagicMock()
        chain = mock_db.table.return_value.select.return_value
        chain.eq.return_value.eq.return_value.range.return_value.execute.return_value = _resp(rows)

        with patch("app.services.event_ledger.get_db", return_value=mock_db):
            result = await get_processed_payment_ids("141air")

        assert result == {100, 200}

    @pytest.mark.asyncio
    async def test_pagination_boundary_1000(self):
        """Exactly 1000 rows triggers a second page fetch."""
        page1 = [{"ml_payment_id": i} for i in range(1000)]
        page2 = [{"ml_payment_id": 1000 + i} for i in range(5)]

        mock_db = MagicMock()
        chain = mock_db.table.return_value.select.return_value
        execute_mock = chain.eq.return_value.eq.return_value.range.return_value.execute
        execute_mock.side_effect = [_resp(page1), _resp(page2)]

        with patch("app.services.event_ledger.get_db", return_value=mock_db):
            result = await get_processed_payment_ids("141air")

        assert len(result) == 1005
        assert 0 in result
        assert 999 in result
        assert 1004 in result

    @pytest.mark.asyncio
    async def test_999_rows_no_second_page(self):
        """999 rows (< page_limit) does NOT trigger second page."""
        page1 = [{"ml_payment_id": i} for i in range(999)]

        mock_db = MagicMock()
        chain = mock_db.table.return_value.select.return_value
        execute_mock = chain.eq.return_value.eq.return_value.range.return_value.execute
        execute_mock.return_value = _resp(page1)

        with patch("app.services.event_ledger.get_db", return_value=mock_db):
            result = await get_processed_payment_ids("141air")

        assert len(result) == 999
        # execute was called only once
        assert execute_mock.call_count == 1

    @pytest.mark.asyncio
    async def test_empty(self):
        mock_db = MagicMock()
        chain = mock_db.table.return_value.select.return_value
        chain.eq.return_value.eq.return_value.range.return_value.execute.return_value = _resp([])

        with patch("app.services.event_ledger.get_db", return_value=mock_db):
            result = await get_processed_payment_ids("141air")

        assert result == set()


# ===========================================================================
# get_processed_payment_ids_in
# ===========================================================================

class TestGetProcessedPaymentIdsInAsync:

    @pytest.mark.asyncio
    async def test_batch_lookup(self):
        rows = [{"ml_payment_id": 100}, {"ml_payment_id": 300}]
        mock_db = MagicMock()
        chain = mock_db.table.return_value.select.return_value
        chain.eq.return_value.eq.return_value.in_.return_value.execute.return_value = _resp(rows)

        with patch("app.services.event_ledger.get_db", return_value=mock_db):
            result = await get_processed_payment_ids_in("141air", [100, 200, 300])

        assert result == {100, 300}

    @pytest.mark.asyncio
    async def test_empty_input(self):
        result = await get_processed_payment_ids_in("141air", [])
        assert result == set()


# ===========================================================================
# get_payment_fees_from_events
# ===========================================================================

class TestGetPaymentFeesAsync:

    @pytest.mark.asyncio
    async def test_derives_fee_and_shipping(self):
        rows = [
            {"ml_payment_id": 100, "event_type": "fee_charged", "signed_amount": -20.0},
            {"ml_payment_id": 100, "event_type": "shipping_charged", "signed_amount": -5.0},
            {"ml_payment_id": 200, "event_type": "fee_charged", "signed_amount": -15.0},
        ]
        mock_db = MagicMock()
        chain = mock_db.table.return_value.select.return_value
        chain.eq.return_value.in_.return_value.in_.return_value.execute.return_value = _resp(rows)

        with patch("app.services.event_ledger.get_db", return_value=mock_db):
            fees = await get_payment_fees_from_events("141air", [100, 200])

        assert fees[100]["fee"] == 20.0
        assert fees[100]["shipping"] == 5.0
        assert fees[200]["fee"] == 15.0
        assert fees[200]["shipping"] == 0.0

    @pytest.mark.asyncio
    async def test_empty_input(self):
        result = await get_payment_fees_from_events("141air", [])
        assert result == {}


# ===========================================================================
# get_payment_statuses
# ===========================================================================

class TestGetPaymentStatusesAsync:

    @pytest.mark.asyncio
    async def test_derives_statuses(self):
        rows = [
            {"ml_payment_id": 100, "event_type": "sale_approved"},
            {"ml_payment_id": 100, "event_type": "ca_sync_completed"},
            {"ml_payment_id": 200, "event_type": "sale_approved"},
            {"ml_payment_id": 200, "event_type": "refund_created"},
            {"ml_payment_id": 300, "event_type": "sale_approved"},
        ]
        mock_db = MagicMock()
        chain = mock_db.table.return_value.select.return_value
        chain.eq.return_value.not_.like.return_value.not_.like.return_value.range.return_value.execute.return_value = _resp(rows)

        with patch("app.services.event_ledger.get_db", return_value=mock_db):
            statuses = await get_payment_statuses("141air")

        assert statuses[100] == "synced"
        assert statuses[200] == "refunded"
        assert statuses[300] == "queued"

    @pytest.mark.asyncio
    async def test_with_date_range(self):
        rows = [
            {"ml_payment_id": 100, "event_type": "sale_approved"},
            {"ml_payment_id": 100, "event_type": "ca_sync_failed"},
        ]
        mock_db = MagicMock()
        chain = mock_db.table.return_value.select.return_value
        # With date_from and date_to, chain has .not_.like().not_.like().gte().lte() before .range()
        chain.eq.return_value.not_.like.return_value.not_.like.return_value.gte.return_value.lte.return_value.range.return_value.execute.return_value = _resp(rows)

        with patch("app.services.event_ledger.get_db", return_value=mock_db):
            statuses = await get_payment_statuses(
                "141air", date_from="2026-01-01", date_to="2026-01-31"
            )

        assert statuses[100] == "error"

    @pytest.mark.asyncio
    async def test_pagination(self):
        page1 = [{"ml_payment_id": i, "event_type": "sale_approved"} for i in range(1000)]
        page2 = [{"ml_payment_id": 1000, "event_type": "sale_approved"}]

        mock_db = MagicMock()
        chain = mock_db.table.return_value.select.return_value
        execute_mock = chain.eq.return_value.not_.like.return_value.not_.like.return_value.range.return_value.execute
        execute_mock.side_effect = [_resp(page1), _resp(page2)]

        with patch("app.services.event_ledger.get_db", return_value=mock_db):
            statuses = await get_payment_statuses("141air")

        assert len(statuses) == 1001
        assert all(s == "queued" for s in statuses.values())

    @pytest.mark.asyncio
    async def test_empty(self):
        mock_db = MagicMock()
        chain = mock_db.table.return_value.select.return_value
        chain.eq.return_value.not_.like.return_value.not_.like.return_value.range.return_value.execute.return_value = _resp([])

        with patch("app.services.event_ledger.get_db", return_value=mock_db):
            statuses = await get_payment_statuses("141air")

        assert statuses == {}
