"""
Integration tests for processor.py — async orchestration with mocked services.

Tests the state machine (8 paths through process_payment_webhook),
filters, subsidy detection, partial refund idempotency,
charged_back+reimbursed handling, and EventRecordError handling.

Run: python3 -m pytest testes/test_processor_integration.py -v
"""
import pytest
from unittest.mock import patch, AsyncMock, MagicMock

from app.services.event_ledger import build_idempotency_key, EventRecordError
from app.services.processor import process_payment_webhook


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SELLER = {
    "slug": "141air",
    "ca_conta_bancaria": "conta-uuid",
    "ca_contato_ml": "contato-uuid",
    "ca_centro_custo_variavel": "cc-uuid",
}

ORDER_RESPONSE = {
    "order_items": [{"item": {"title": "Test Widget"}}],
}


def _make_payment(
    payment_id=12345,
    status="approved",
    status_detail="accredited",
    amount=100.0,
    net=80.0,
    fee_original=15.0,
    shipping_original=5.0,
    shipping_amount=0,
    order_id="2000001",
    order_type="mercadolibre",
    date_approved="2026-01-15T10:00:00.000-04:00",
    money_release_date="2026-01-20T10:00:00.000-04:00",
    refunds=None,
    collector_id=None,
    description=None,
    transaction_amount_refunded=None,
    fee_refunded=None,
    shipping_refunded=None,
):
    charges = []
    if fee_original > 0:
        charges.append({
            "accounts": {"from": "collector", "to": "ml"},
            "amounts": {"original": fee_original, "refunded": fee_refunded or 0},
            "name": "ml_sale_fee",
            "type": "fee",
        })
    if shipping_original > 0:
        charges.append({
            "accounts": {"from": "collector", "to": "1745333938"},
            "amounts": {"original": shipping_original, "refunded": shipping_refunded or 0},
            "name": "shp_fulfillment",
            "type": "shipping",
            "metadata": {"shipment_id": 46187498511},
        })

    payment = {
        "id": payment_id,
        "status": status,
        "status_detail": status_detail,
        "transaction_amount": amount,
        "shipping_amount": shipping_amount,
        "transaction_details": {"net_received_amount": net},
        "charges_details": charges,
        "order": {"id": order_id, "type": order_type} if order_id else None,
        "money_release_date": money_release_date,
        "date_approved": date_approved,
        "date_created": date_approved,
    }
    if refunds is not None:
        payment["refunds"] = refunds
    if transaction_amount_refunded is not None:
        payment["transaction_amount_refunded"] = transaction_amount_refunded
    if collector_id is not None:
        payment["collector"] = {"id": collector_id}
    if description is not None:
        payment["description"] = description
    return payment


def _recorded_events(mock_el):
    """Extract event_type from all record_event calls."""
    return [c.kwargs["event_type"] for c in mock_el.record_event.call_args_list]


def _recorded_event_amounts(mock_el):
    """Extract (event_type, signed_amount) from all record_event calls."""
    return [
        (c.kwargs["event_type"], c.kwargs["signed_amount"])
        for c in mock_el.record_event.call_args_list
    ]


# ---------------------------------------------------------------------------
# Fixture: shared mocks for processor
# ---------------------------------------------------------------------------

@pytest.fixture
def proc_mocks():
    with patch("app.services.processor.get_db") as mock_get_db, \
         patch("app.services.processor.get_seller_config") as mock_gsc, \
         patch("app.services.processor.get_missing_ca_launch_fields") as mock_gmclf, \
         patch("app.services.processor.ml_api") as mock_ml, \
         patch("app.services.processor.ca_queue") as mock_cq, \
         patch("app.services.processor.event_ledger") as mock_el:

        mock_gsc.return_value = SELLER
        mock_gmclf.return_value = []

        # event_ledger async functions
        mock_el.get_events = AsyncMock(return_value=[])
        mock_el.record_event = AsyncMock(return_value={"id": 1})
        mock_el.build_idempotency_key = build_idempotency_key

        # ml_api
        mock_ml.get_order = AsyncMock(return_value=ORDER_RESPONSE)
        mock_ml.get_payment = AsyncMock()

        # ca_queue — all async
        mock_cq.enqueue_receita = AsyncMock(return_value={})
        mock_cq.enqueue_comissao = AsyncMock(return_value={})
        mock_cq.enqueue_frete = AsyncMock(return_value={})
        mock_cq.enqueue_estorno = AsyncMock(return_value={})
        mock_cq.enqueue_estorno_taxa = AsyncMock(return_value={})
        mock_cq.enqueue_estorno_frete = AsyncMock(return_value={})
        mock_cq.enqueue_partial_refund = AsyncMock(return_value={})

        yield {
            "get_db": mock_get_db,
            "get_seller_config": mock_gsc,
            "get_missing_ca_launch_fields": mock_gmclf,
            "ml_api": mock_ml,
            "ca_queue": mock_cq,
            "event_ledger": mock_el,
        }


# ===========================================================================
# State Machine — 8 paths through process_payment_webhook
# ===========================================================================

class TestStateMachine:

    @pytest.mark.asyncio
    async def test_approved_new_creates_all_events(self, proc_mocks):
        """Approved payment with no existing events → sale + fee + shipping."""
        payment = _make_payment()
        m = proc_mocks

        await process_payment_webhook("141air", 12345, payment_data=payment)

        events = _recorded_events(m["event_ledger"])
        assert "sale_approved" in events
        assert "fee_charged" in events
        assert "shipping_charged" in events
        m["ca_queue"].enqueue_receita.assert_called_once()
        m["ca_queue"].enqueue_comissao.assert_called_once()
        m["ca_queue"].enqueue_frete.assert_called_once()

    @pytest.mark.asyncio
    async def test_approved_already_processed_skips(self, proc_mocks):
        """Approved payment with existing sale_approved → skip (no new events)."""
        payment = _make_payment()
        m = proc_mocks
        m["event_ledger"].get_events = AsyncMock(return_value=[
            {"event_type": "sale_approved"},
        ])

        await process_payment_webhook("141air", 12345, payment_data=payment)

        m["event_ledger"].record_event.assert_not_called()
        m["ca_queue"].enqueue_receita.assert_not_called()

    @pytest.mark.asyncio
    async def test_approved_partially_refunded_creates_partial_refund(self, proc_mocks):
        """Approved + partially_refunded → _process_partial_refund."""
        payment = _make_payment(
            status_detail="partially_refunded",
            refunds=[{"id": 9001, "amount": 30.0, "date_created": "2026-01-25T10:00:00.000-04:00"}],
        )
        m = proc_mocks
        # First call (process_payment_webhook): existing events with sale_approved
        # Second call (_process_partial_refund): same events (no partial_refund yet)
        m["event_ledger"].get_events = AsyncMock(side_effect=[
            [{"event_type": "sale_approved"}],
            [{"event_type": "sale_approved"}],
        ])

        await process_payment_webhook("141air", 12345, payment_data=payment)

        events = _recorded_events(m["event_ledger"])
        assert "partial_refund" in events
        m["ca_queue"].enqueue_partial_refund.assert_called_once()

    @pytest.mark.asyncio
    async def test_refunded_never_processed_creates_approval_first(self, proc_mocks):
        """Refunded payment with no existing events → approval + refund."""
        payment = _make_payment(
            status="refunded", status_detail="bpp_refunded",
            refunds=[{"id": 1, "amount": 100.0, "date_created": "2026-02-01T10:00:00.000-04:00"}],
            transaction_amount_refunded=100.0,
            fee_refunded=15.0, shipping_refunded=5.0,
        )
        m = proc_mocks

        await process_payment_webhook("141air", 12345, payment_data=payment)

        events = _recorded_events(m["event_ledger"])
        # Must create approval events first, then refund events
        assert "sale_approved" in events
        assert "refund_created" in events
        m["ca_queue"].enqueue_receita.assert_called_once()
        m["ca_queue"].enqueue_estorno.assert_called_once()

    @pytest.mark.asyncio
    async def test_refunded_already_approved_only_refunds(self, proc_mocks):
        """Refunded with existing sale_approved → only refund events."""
        payment = _make_payment(
            status="refunded", status_detail="bpp_refunded",
            refunds=[{"id": 1, "amount": 100.0, "date_created": "2026-02-01T10:00:00.000-04:00"}],
            transaction_amount_refunded=100.0,
            fee_refunded=15.0, shipping_refunded=5.0,
        )
        m = proc_mocks
        m["event_ledger"].get_events = AsyncMock(return_value=[
            {"event_type": "sale_approved"},
            {"event_type": "fee_charged"},
        ])

        await process_payment_webhook("141air", 12345, payment_data=payment)

        events = _recorded_events(m["event_ledger"])
        assert "sale_approved" not in events  # NOT created again
        assert "refund_created" in events
        m["ca_queue"].enqueue_receita.assert_not_called()
        m["ca_queue"].enqueue_estorno.assert_called_once()

    @pytest.mark.asyncio
    async def test_charged_back_reimbursed_treated_as_approved(self, proc_mocks):
        """charged_back + reimbursed → _process_approved (NOT refund)."""
        payment = _make_payment(
            status="charged_back", status_detail="reimbursed",
        )
        m = proc_mocks

        await process_payment_webhook("141air", 12345, payment_data=payment)

        events = _recorded_events(m["event_ledger"])
        assert "sale_approved" in events
        assert "refund_created" not in events
        assert "charged_back" not in events
        m["ca_queue"].enqueue_receita.assert_called_once()
        m["ca_queue"].enqueue_estorno.assert_not_called()

    @pytest.mark.asyncio
    async def test_refunded_by_admin_no_existing_skips(self, proc_mocks):
        """refunded/by_admin with no sale_approved → skip (kit split)."""
        payment = _make_payment(
            status="refunded", status_detail="by_admin",
        )
        m = proc_mocks

        await process_payment_webhook("141air", 12345, payment_data=payment)

        m["event_ledger"].record_event.assert_not_called()
        m["ca_queue"].enqueue_receita.assert_not_called()
        m["ca_queue"].enqueue_estorno.assert_not_called()

    @pytest.mark.asyncio
    async def test_refunded_by_admin_with_existing_processes_refund(self, proc_mocks):
        """refunded/by_admin with existing sale_approved → process refund."""
        payment = _make_payment(
            status="refunded", status_detail="by_admin",
            refunds=[{"id": 1, "amount": 100.0, "date_created": "2026-02-01T10:00:00.000-04:00"}],
            transaction_amount_refunded=100.0,
            fee_refunded=15.0,
        )
        m = proc_mocks
        m["event_ledger"].get_events = AsyncMock(return_value=[
            {"event_type": "sale_approved"},
        ])

        await process_payment_webhook("141air", 12345, payment_data=payment)

        events = _recorded_events(m["event_ledger"])
        assert "refund_created" in events
        m["ca_queue"].enqueue_estorno.assert_called_once()


# ===========================================================================
# Filters — payments that should be skipped
# ===========================================================================

class TestFilters:

    @pytest.mark.asyncio
    async def test_no_order_id_skips(self, proc_mocks):
        payment = _make_payment(order_id=None)
        m = proc_mocks

        await process_payment_webhook("141air", 12345, payment_data=payment)

        m["event_ledger"].record_event.assert_not_called()

    @pytest.mark.asyncio
    async def test_marketplace_shipment_skips(self, proc_mocks):
        payment = _make_payment(description="marketplace_shipment")
        m = proc_mocks

        await process_payment_webhook("141air", 12345, payment_data=payment)

        m["event_ledger"].record_event.assert_not_called()

    @pytest.mark.asyncio
    async def test_collector_id_skips(self, proc_mocks):
        payment = _make_payment(collector_id=999)
        m = proc_mocks

        await process_payment_webhook("141air", 12345, payment_data=payment)

        m["event_ledger"].record_event.assert_not_called()

    @pytest.mark.asyncio
    async def test_cancelled_skips(self, proc_mocks):
        payment = _make_payment(status="cancelled")
        m = proc_mocks

        await process_payment_webhook("141air", 12345, payment_data=payment)

        m["event_ledger"].record_event.assert_not_called()

    @pytest.mark.asyncio
    async def test_missing_ca_config_skips(self, proc_mocks):
        payment = _make_payment()
        m = proc_mocks
        m["get_missing_ca_launch_fields"].return_value = ["ca_conta_bancaria"]

        await process_payment_webhook("141air", 12345, payment_data=payment)

        m["event_ledger"].record_event.assert_not_called()

    @pytest.mark.asyncio
    async def test_seller_not_found_skips(self, proc_mocks):
        payment = _make_payment()
        m = proc_mocks
        m["get_seller_config"].return_value = None

        await process_payment_webhook("141air", 12345, payment_data=payment)

        m["event_ledger"].record_event.assert_not_called()


# ===========================================================================
# Subsidy detection
# ===========================================================================

class TestSubsidy:

    @pytest.mark.asyncio
    async def test_subsidy_when_net_exceeds_calc(self, proc_mocks):
        """When ML net > calculated net (fee + shipping), subsidy_credited is emitted."""
        # amount=100, fee=15, shipping=5 → calculated net = 80
        # But actual net = 82 → subsidy = 2.0
        payment = _make_payment(amount=100.0, net=82.0, fee_original=15.0, shipping_original=5.0)
        m = proc_mocks

        await process_payment_webhook("141air", 12345, payment_data=payment)

        events = _recorded_event_amounts(m["event_ledger"])
        subsidy_events = [(et, amt) for et, amt in events if et == "subsidy_credited"]
        assert len(subsidy_events) == 1
        assert subsidy_events[0][1] == 2.0

    @pytest.mark.asyncio
    async def test_no_subsidy_when_net_matches(self, proc_mocks):
        """When ML net matches calculated net, no subsidy event."""
        payment = _make_payment(amount=100.0, net=80.0, fee_original=15.0, shipping_original=5.0)
        m = proc_mocks

        await process_payment_webhook("141air", 12345, payment_data=payment)

        events = _recorded_events(m["event_ledger"])
        assert "subsidy_credited" not in events


# ===========================================================================
# Partial refund idempotency
# ===========================================================================

class TestPartialRefundIdempotency:

    @pytest.mark.asyncio
    async def test_skips_already_processed_refunds(self, proc_mocks):
        """Re-run with existing partial_refund events → skips already-processed indices."""
        payment = _make_payment(
            status_detail="partially_refunded",
            refunds=[
                {"id": 9001, "amount": 20.0, "date_created": "2026-01-25T10:00:00.000-04:00"},
                {"id": 9002, "amount": 30.0, "date_created": "2026-01-26T10:00:00.000-04:00"},
            ],
        )
        m = proc_mocks
        # First call: process_payment_webhook gets existing events
        # Second call: _process_partial_refund gets existing events (1 partial_refund already done)
        m["event_ledger"].get_events = AsyncMock(side_effect=[
            [{"event_type": "sale_approved"}, {"event_type": "partial_refund"}],
            [{"event_type": "sale_approved"}, {"event_type": "partial_refund"}],
        ])

        await process_payment_webhook("141air", 12345, payment_data=payment)

        # Only the second refund (index 1) should be processed
        events = _recorded_events(m["event_ledger"])
        assert events.count("partial_refund") == 1
        m["ca_queue"].enqueue_partial_refund.assert_called_once()

    @pytest.mark.asyncio
    async def test_processes_all_new_refunds(self, proc_mocks):
        """First run with 2 refunds and no existing → processes both."""
        payment = _make_payment(
            status_detail="partially_refunded",
            refunds=[
                {"id": 9001, "amount": 20.0, "date_created": "2026-01-25T10:00:00.000-04:00"},
                {"id": 9002, "amount": 30.0, "date_created": "2026-01-26T10:00:00.000-04:00"},
            ],
        )
        m = proc_mocks
        m["event_ledger"].get_events = AsyncMock(side_effect=[
            [{"event_type": "sale_approved"}],  # process_payment_webhook
            [{"event_type": "sale_approved"}],  # _process_partial_refund (no partial_refund yet)
        ])

        await process_payment_webhook("141air", 12345, payment_data=payment)

        events = _recorded_events(m["event_ledger"])
        assert events.count("partial_refund") == 2
        assert m["ca_queue"].enqueue_partial_refund.call_count == 2


# ===========================================================================
# Refund with estorno taxa/frete
# ===========================================================================

class TestRefundEstornos:

    @pytest.mark.asyncio
    async def test_full_refund_creates_estorno_taxa_and_frete(self, proc_mocks):
        """Full refund with refunded charges → refund_fee + refund_shipping events."""
        payment = _make_payment(
            status="refunded", status_detail="bpp_refunded",
            amount=100.0, net=80.0,
            fee_original=15.0, shipping_original=5.0,
            fee_refunded=15.0, shipping_refunded=5.0,
            refunds=[{"id": 1, "amount": 100.0, "date_created": "2026-02-01T10:00:00.000-04:00"}],
            transaction_amount_refunded=100.0,
        )
        m = proc_mocks
        m["event_ledger"].get_events = AsyncMock(return_value=[
            {"event_type": "sale_approved"},
        ])

        await process_payment_webhook("141air", 12345, payment_data=payment)

        events = _recorded_events(m["event_ledger"])
        assert "refund_created" in events
        assert "refund_fee" in events
        assert "refund_shipping" in events
        m["ca_queue"].enqueue_estorno_taxa.assert_called_once()
        m["ca_queue"].enqueue_estorno_frete.assert_called_once()

    @pytest.mark.asyncio
    async def test_partial_refund_with_refunded_fees_creates_estorno_taxa(self, proc_mocks):
        """Partial refund where ML refunded fees → refund_fee + refund_shipping events ARE created."""
        payment = _make_payment(
            status="refunded", status_detail="bpp_refunded",
            amount=100.0, net=80.0,
            fee_original=15.0, shipping_original=5.0,
            fee_refunded=15.0, shipping_refunded=5.0,
            refunds=[{"id": 1, "amount": 50.0, "date_created": "2026-02-01T10:00:00.000-04:00"}],
            transaction_amount_refunded=50.0,
        )
        m = proc_mocks
        m["event_ledger"].get_events = AsyncMock(return_value=[
            {"event_type": "sale_approved"},
        ])

        await process_payment_webhook("141air", 12345, payment_data=payment)

        events = _recorded_events(m["event_ledger"])
        assert "refund_created" in events
        # ML reported refunded fees → events must be created regardless of partial refund
        assert "refund_fee" in events
        assert "refund_shipping" in events
        m["ca_queue"].enqueue_estorno_taxa.assert_called_once()
        m["ca_queue"].enqueue_estorno_frete.assert_called_once()

    @pytest.mark.asyncio
    async def test_partial_refund_without_refunded_fees_no_estorno_taxa(self, proc_mocks):
        """Partial refund where ML did NOT refund fees → no refund_fee or refund_shipping."""
        payment = _make_payment(
            status="refunded", status_detail="bpp_refunded",
            amount=100.0, net=80.0,
            fee_original=15.0, shipping_original=5.0,
            fee_refunded=0, shipping_refunded=0,
            refunds=[{"id": 1, "amount": 50.0, "date_created": "2026-02-01T10:00:00.000-04:00"}],
            transaction_amount_refunded=50.0,
        )
        m = proc_mocks
        m["event_ledger"].get_events = AsyncMock(return_value=[
            {"event_type": "sale_approved"},
        ])

        await process_payment_webhook("141air", 12345, payment_data=payment)

        events = _recorded_events(m["event_ledger"])
        assert "refund_created" in events
        # ML reported zero refunded fees → no taxa/frete events
        assert "refund_fee" not in events
        assert "refund_shipping" not in events
        m["ca_queue"].enqueue_estorno_taxa.assert_not_called()
        m["ca_queue"].enqueue_estorno_frete.assert_not_called()


# ===========================================================================
# Error handling
# ===========================================================================

class TestErrorHandling:

    @pytest.mark.asyncio
    async def test_event_record_error_skips_enqueue(self, proc_mocks):
        """EventRecordError on record_event → corresponding enqueue is NOT called."""
        payment = _make_payment()
        m = proc_mocks
        # All record_event calls raise EventRecordError
        m["event_ledger"].record_event = AsyncMock(
            side_effect=EventRecordError("DB connection lost"),
        )

        # Should not raise — errors are caught and logged
        await process_payment_webhook("141air", 12345, payment_data=payment)

        # record_event was attempted for all 3 events
        assert m["event_ledger"].record_event.call_count == 3
        # No enqueue calls since all record_events failed (WAL pattern)
        m["ca_queue"].enqueue_receita.assert_not_called()
        m["ca_queue"].enqueue_comissao.assert_not_called()
        m["ca_queue"].enqueue_frete.assert_not_called()

    @pytest.mark.asyncio
    async def test_partial_record_failure_skips_only_failed_enqueue(self, proc_mocks):
        """When one record_event fails, only that pair's enqueue is skipped."""
        payment = _make_payment()
        m = proc_mocks
        # sale_approved succeeds, fee_charged fails, shipping_charged succeeds
        m["event_ledger"].record_event = AsyncMock(side_effect=[
            {"id": 1},
            EventRecordError("DB connection lost"),
            {"id": 3},
        ])

        await process_payment_webhook("141air", 12345, payment_data=payment)

        assert m["event_ledger"].record_event.call_count == 3
        # receita enqueued (sale_approved succeeded)
        m["ca_queue"].enqueue_receita.assert_called_once()
        # comissao NOT enqueued (fee_charged failed)
        m["ca_queue"].enqueue_comissao.assert_not_called()
        # frete enqueued (shipping_charged succeeded)
        m["ca_queue"].enqueue_frete.assert_called_once()

    @pytest.mark.asyncio
    async def test_enqueue_failure_after_successful_record(self, proc_mocks):
        """If enqueue fails after record_event succeeds, event is still recorded."""
        payment = _make_payment()
        m = proc_mocks
        # All record_event calls succeed
        m["event_ledger"].record_event = AsyncMock(return_value={"id": 1})
        # enqueue_receita fails
        m["ca_queue"].enqueue_receita = AsyncMock(side_effect=Exception("Queue down"))

        # Should not raise — enqueue failure is caught and logged
        await process_payment_webhook("141air", 12345, payment_data=payment)

        # record_event was called successfully for all events
        events = _recorded_events(m["event_ledger"])
        assert "sale_approved" in events
        assert "fee_charged" in events
        assert "shipping_charged" in events
        # enqueue_receita was attempted (and failed)
        m["ca_queue"].enqueue_receita.assert_called_once()
        # Other enqueues still succeeded
        m["ca_queue"].enqueue_comissao.assert_called_once()
        m["ca_queue"].enqueue_frete.assert_called_once()

    @pytest.mark.asyncio
    async def test_refund_already_exists_skips(self, proc_mocks):
        """If refund_created already exists, _process_refunded skips entirely."""
        payment = _make_payment(
            status="refunded", status_detail="bpp_refunded",
            refunds=[{"id": 1, "amount": 100.0, "date_created": "2026-02-01T10:00:00.000-04:00"}],
        )
        m = proc_mocks
        m["event_ledger"].get_events = AsyncMock(return_value=[
            {"event_type": "sale_approved"},
            {"event_type": "refund_created"},
        ])

        await process_payment_webhook("141air", 12345, payment_data=payment)

        m["event_ledger"].record_event.assert_not_called()
        m["ca_queue"].enqueue_estorno.assert_not_called()


# ===========================================================================
# No shipping / no fee edge cases
# ===========================================================================

class TestEdgeCases:

    @pytest.mark.asyncio
    async def test_zero_fee_no_comissao_enqueued(self, proc_mocks):
        """When fee = 0, no comissao job or fee_charged event."""
        payment = _make_payment(
            amount=100.0, net=95.0,
            fee_original=0, shipping_original=5.0,
        )
        m = proc_mocks

        await process_payment_webhook("141air", 12345, payment_data=payment)

        events = _recorded_events(m["event_ledger"])
        assert "sale_approved" in events
        assert "fee_charged" not in events
        assert "shipping_charged" in events
        m["ca_queue"].enqueue_comissao.assert_not_called()

    @pytest.mark.asyncio
    async def test_zero_shipping_no_frete_enqueued(self, proc_mocks):
        """When shipping = 0, no frete job or shipping_charged event."""
        payment = _make_payment(
            amount=100.0, net=85.0,
            fee_original=15.0, shipping_original=0,
        )
        m = proc_mocks

        await process_payment_webhook("141air", 12345, payment_data=payment)

        events = _recorded_events(m["event_ledger"])
        assert "sale_approved" in events
        assert "fee_charged" in events
        assert "shipping_charged" not in events
        m["ca_queue"].enqueue_frete.assert_not_called()

    @pytest.mark.asyncio
    async def test_mercadopago_order_type(self, proc_mocks):
        """order_type=mercadopago uses 1.1.2 category (venda_ecommerce)."""
        payment = _make_payment(order_type="mercadopago")
        m = proc_mocks

        await process_payment_webhook("141air", 12345, payment_data=payment)

        # Verify the receita was enqueued (it uses the right category internally)
        m["ca_queue"].enqueue_receita.assert_called_once()
        events = _recorded_events(m["event_ledger"])
        assert "sale_approved" in events
