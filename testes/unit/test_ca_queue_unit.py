"""
Unit tests for ca_queue.py — enqueue idempotency, convenience wrappers,
and CaWorker._check_group_completion logic.

Run: python3 -m pytest testes/test_ca_queue_unit.py -v
"""
import pytest
from unittest.mock import patch, AsyncMock, MagicMock
from types import SimpleNamespace

from app.services.ca_queue import (
    enqueue,
    enqueue_receita,
    enqueue_comissao,
    enqueue_frete,
    enqueue_estorno,
    enqueue_estorno_taxa,
    enqueue_estorno_frete,
    enqueue_partial_refund,
    enqueue_baixa,
    CaWorker,
)
from app.services.event_ledger import EventRecordError


def _resp(data=None, count=None):
    return SimpleNamespace(data=data or [], count=count)


# ===========================================================================
# enqueue
# ===========================================================================

class TestEnqueue:

    @pytest.mark.asyncio
    async def test_success(self):
        mock_db = MagicMock()
        inserted = {"id": "job-1", "status": "pending", "idempotency_key": "141air:100:receita"}
        mock_db.table.return_value.insert.return_value.execute.return_value = _resp([inserted])

        with patch("app.services.ca_queue.get_db", return_value=mock_db):
            result = await enqueue(
                seller_slug="141air",
                job_type="receita",
                ca_endpoint="/v1/test",
                ca_payload={"valor": 100},
                idempotency_key="141air:100:receita",
            )

        assert result["status"] == "pending"
        mock_db.table.assert_called_with("ca_jobs")

    @pytest.mark.asyncio
    async def test_idempotency_conflict_returns_existing(self):
        mock_db = MagicMock()
        # insert raises duplicate key error
        mock_db.table.return_value.insert.return_value.execute.side_effect = Exception(
            "duplicate key value violates unique constraint"
        )
        # select returns existing job
        existing = {"id": "job-1", "status": "completed", "idempotency_key": "141air:100:receita"}
        mock_db.table.return_value.select.return_value.eq.return_value.execute.return_value = _resp([existing])

        with patch("app.services.ca_queue.get_db", return_value=mock_db):
            result = await enqueue(
                seller_slug="141air",
                job_type="receita",
                ca_endpoint="/v1/test",
                ca_payload={"valor": 100},
                idempotency_key="141air:100:receita",
            )

        assert result["status"] == "completed"

    @pytest.mark.asyncio
    async def test_non_duplicate_error_propagates(self):
        mock_db = MagicMock()
        mock_db.table.return_value.insert.return_value.execute.side_effect = Exception(
            "connection timeout"
        )

        with patch("app.services.ca_queue.get_db", return_value=mock_db):
            with pytest.raises(Exception, match="connection timeout"):
                await enqueue(
                    seller_slug="141air",
                    job_type="receita",
                    ca_endpoint="/v1/test",
                    ca_payload={"valor": 100},
                    idempotency_key="141air:100:receita",
                )


# ===========================================================================
# Convenience wrappers — verify correct parameters
# ===========================================================================

class TestConvenienceWrappers:

    @pytest.mark.asyncio
    async def test_enqueue_receita_priority_10(self):
        mock_db = MagicMock()
        mock_db.table.return_value.insert.return_value.execute.return_value = _resp([{"id": 1}])

        with patch("app.services.ca_queue.get_db", return_value=mock_db):
            await enqueue_receita("141air", 100, {"valor": 50})

        row = mock_db.table.return_value.insert.call_args[0][0]
        assert row["priority"] == 10
        assert row["job_type"] == "receita"
        assert row["idempotency_key"] == "141air:100:receita"
        assert "contas-a-receber" in row["ca_endpoint"]

    @pytest.mark.asyncio
    async def test_enqueue_comissao_priority_20(self):
        mock_db = MagicMock()
        mock_db.table.return_value.insert.return_value.execute.return_value = _resp([{"id": 1}])

        with patch("app.services.ca_queue.get_db", return_value=mock_db):
            await enqueue_comissao("141air", 100, {"valor": 10})

        row = mock_db.table.return_value.insert.call_args[0][0]
        assert row["priority"] == 20
        assert row["idempotency_key"] == "141air:100:comissao"
        assert "contas-a-pagar" in row["ca_endpoint"]

    @pytest.mark.asyncio
    async def test_enqueue_frete_priority_20(self):
        mock_db = MagicMock()
        mock_db.table.return_value.insert.return_value.execute.return_value = _resp([{"id": 1}])

        with patch("app.services.ca_queue.get_db", return_value=mock_db):
            await enqueue_frete("141air", 100, {"valor": 5})

        row = mock_db.table.return_value.insert.call_args[0][0]
        assert row["priority"] == 20
        assert row["idempotency_key"] == "141air:100:frete"

    @pytest.mark.asyncio
    async def test_enqueue_partial_refund_includes_index(self):
        mock_db = MagicMock()
        mock_db.table.return_value.insert.return_value.execute.return_value = _resp([{"id": 1}])

        with patch("app.services.ca_queue.get_db", return_value=mock_db):
            await enqueue_partial_refund("141air", 100, 2, {"valor": 30})

        row = mock_db.table.return_value.insert.call_args[0][0]
        assert row["idempotency_key"] == "141air:100:partial_refund:2"

    @pytest.mark.asyncio
    async def test_enqueue_estorno_taxa_uses_receber(self):
        mock_db = MagicMock()
        mock_db.table.return_value.insert.return_value.execute.return_value = _resp([{"id": 1}])

        with patch("app.services.ca_queue.get_db", return_value=mock_db):
            await enqueue_estorno_taxa("141air", 100, {"valor": 15})

        row = mock_db.table.return_value.insert.call_args[0][0]
        assert "contas-a-receber" in row["ca_endpoint"]

    @pytest.mark.asyncio
    async def test_enqueue_baixa_priority_30_with_scheduled(self):
        mock_db = MagicMock()
        mock_db.table.return_value.insert.return_value.execute.return_value = _resp([{"id": 1}])

        with patch("app.services.ca_queue.get_db", return_value=mock_db):
            await enqueue_baixa("141air", "parcela-uuid", {"valor": 100}, scheduled_for="2026-01-20T00:00:00Z")

        row = mock_db.table.return_value.insert.call_args[0][0]
        assert row["priority"] == 30
        assert row["scheduled_for"] == "2026-01-20T00:00:00Z"
        assert "parcela-uuid" in row["ca_endpoint"]


# ===========================================================================
# CaWorker._check_group_completion
# ===========================================================================

class TestCheckGroupCompletion:

    @pytest.mark.asyncio
    async def test_all_completed_records_sync_completed(self):
        """All jobs in group completed → ca_sync_completed event."""
        mock_db = MagicMock()
        table_mock = mock_db.table.return_value
        sel_mock = table_mock.select.return_value

        # dead query returns count=0
        sel_mock.eq.return_value.eq.return_value.execute.return_value = _resp(count=0)
        # pending (neq) query returns count=0
        sel_mock.eq.return_value.neq.return_value.execute.return_value = _resp(count=0)

        worker = CaWorker()

        with patch("app.services.ca_queue.get_db", return_value=mock_db), \
             patch("app.services.ca_queue.event_ledger") as mock_el:
            mock_el.record_event = AsyncMock(return_value={"id": 1})
            await worker._check_group_completion("141air:12345")

        mock_el.record_event.assert_called_once()
        call_kwargs = mock_el.record_event.call_args.kwargs
        assert call_kwargs["event_type"] == "ca_sync_completed"
        assert call_kwargs["seller_slug"] == "141air"
        assert call_kwargs["ml_payment_id"] == 12345

    @pytest.mark.asyncio
    async def test_dead_jobs_record_sync_failed(self):
        """Dead jobs in group → ca_sync_failed event."""
        mock_db = MagicMock()
        table_mock = mock_db.table.return_value
        sel_mock = table_mock.select.return_value

        # dead query returns count=2
        sel_mock.eq.return_value.eq.return_value.execute.return_value = _resp(count=2)

        worker = CaWorker()

        with patch("app.services.ca_queue.get_db", return_value=mock_db), \
             patch("app.services.ca_queue.event_ledger") as mock_el:
            mock_el.record_event = AsyncMock(return_value={"id": 1})
            await worker._check_group_completion("141air:12345")

        mock_el.record_event.assert_called_once()
        call_kwargs = mock_el.record_event.call_args.kwargs
        assert call_kwargs["event_type"] == "ca_sync_failed"

    @pytest.mark.asyncio
    async def test_pending_jobs_no_action(self):
        """Non-completed (pending) jobs remain → no event recorded."""
        mock_db = MagicMock()
        table_mock = mock_db.table.return_value
        sel_mock = table_mock.select.return_value

        # dead query returns count=0
        sel_mock.eq.return_value.eq.return_value.execute.return_value = _resp(count=0)
        # pending (neq) query returns count=1 (still has pending)
        sel_mock.eq.return_value.neq.return_value.execute.return_value = _resp(count=1)

        worker = CaWorker()

        with patch("app.services.ca_queue.get_db", return_value=mock_db), \
             patch("app.services.ca_queue.event_ledger") as mock_el:
            mock_el.record_event = AsyncMock()
            await worker._check_group_completion("141air:12345")

        mock_el.record_event.assert_not_called()

    @pytest.mark.asyncio
    async def test_event_record_error_on_sync_completed(self):
        """EventRecordError on ca_sync_completed is caught (no crash)."""
        mock_db = MagicMock()
        table_mock = mock_db.table.return_value
        sel_mock = table_mock.select.return_value

        sel_mock.eq.return_value.eq.return_value.execute.return_value = _resp(count=0)
        sel_mock.eq.return_value.neq.return_value.execute.return_value = _resp(count=0)

        worker = CaWorker()

        with patch("app.services.ca_queue.get_db", return_value=mock_db), \
             patch("app.services.ca_queue.event_ledger") as mock_el:
            mock_el.record_event = AsyncMock(side_effect=EventRecordError("DB down"))
            # Should not raise
            await worker._check_group_completion("141air:12345")

        mock_el.record_event.assert_called_once()
