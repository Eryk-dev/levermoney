"""
Unit tests for processor.py — the CORE financial logic.

Tests pure functions only (no DB, no API calls). Every test validates
the math that turns an ML payment into CA financial events.

Run: python3 -m pytest testes/test_processor_unit.py -v
"""
import pytest

from app.services.processor import (
    _to_float,
    _to_brt_date,
    _compute_effective_net_amount,
    _extract_processor_charges,
    _build_parcela,
    _build_evento,
    _build_despesa_payload,
)
from app.models.sellers import CA_CATEGORIES


# ===========================================================================
# _to_float
# ===========================================================================

class TestToFloat:
    def test_normal_float(self):
        assert _to_float(66.3) == 66.3

    def test_string_number(self):
        assert _to_float("123.45") == 123.45

    def test_int(self):
        assert _to_float(100) == 100.0

    def test_none(self):
        assert _to_float(None) == 0.0

    def test_empty_string(self):
        assert _to_float("") == 0.0

    def test_invalid_string(self):
        assert _to_float("abc") == 0.0

    def test_zero(self):
        assert _to_float(0) == 0.0


# ===========================================================================
# _to_brt_date
# ===========================================================================

class TestToBrtDate:
    def test_utc_minus_4_same_day(self):
        """ML API returns UTC-4. 10:00 UTC-4 = 13:00 UTC = 10:00 BRT(UTC-3)."""
        assert _to_brt_date("2026-01-01T10:00:00.000-04:00") == "2026-01-01"

    def test_midnight_crossing(self):
        """23:45 UTC-4 = 00:45 BRT next day. Must attribute to Jan 2."""
        assert _to_brt_date("2026-01-01T23:45:00.000-04:00") == "2026-01-02"

    def test_early_morning(self):
        """00:30 UTC-4 = 01:30 BRT. Same day."""
        assert _to_brt_date("2026-01-15T00:30:00.000-04:00") == "2026-01-15"

    def test_real_payment_date(self):
        """Real date from sample payment 140282341986."""
        assert _to_brt_date("2026-01-01T07:58:44.000-04:00") == "2026-01-01"

    def test_invalid_string_fallback(self):
        """Invalid ISO string falls back to first 10 chars."""
        assert _to_brt_date("2026-01-01") == "2026-01-01"

    def test_none_input(self):
        """None input raises TypeError — callers must provide valid string."""
        with pytest.raises(TypeError):
            _to_brt_date(None)


# ===========================================================================
# _extract_processor_charges — THE critical math function
# ===========================================================================

class TestExtractProcessorCharges:
    """Tests the core fee/shipping extraction logic.

    This function is THE source of truth for how much comissao and frete
    a seller pays per payment. Errors here propagate to every CA entry.
    """

    def test_fees_only_no_shipping(self, approved_payment_fees_only):
        """Payment with only fee charges → mp_fee=20.62, shipping=0."""
        mp_fee, shipping, ship_id, reconciled_net, net_diff = _extract_processor_charges(
            approved_payment_fees_only
        )
        assert mp_fee == 20.62
        assert shipping == 0.0
        assert ship_id is None
        assert reconciled_net == 45.68  # 66.3 - 20.62
        assert abs(net_diff) < 0.01  # matches net_received_amount

    def test_fees_plus_shipping(self, approved_payment_with_shipping):
        """Payment with shipping + fees → correct split."""
        mp_fee, shipping, ship_id, reconciled_net, net_diff = _extract_processor_charges(
            approved_payment_with_shipping
        )
        assert mp_fee == 44.23  # 0.23 + 44.00
        assert shipping == 23.45
        assert ship_id == "46187498511"
        assert reconciled_net == 192.52  # 260.2 - 44.23 - 23.45
        assert abs(net_diff) < 0.01

    def test_coupon_from_ml_excluded(self, approved_payment_with_shipping):
        """Coupon from=ml is NOT counted (ML pays, not seller)."""
        mp_fee, _, _, _, _ = _extract_processor_charges(approved_payment_with_shipping)
        # Coupon is 30.0 from=ml → excluded
        # Only fees from collector: 0.23 + 44.00
        assert mp_fee == 44.23

    def test_coupon_from_collector_included(self, payment_with_coupon_from_collector):
        """Coupon from=collector IS counted (seller-funded coupon)."""
        mp_fee, _, _, reconciled_net, _ = _extract_processor_charges(
            payment_with_coupon_from_collector
        )
        # coupon 20 + fee 5 + fee 35 = 60
        assert mp_fee == 60.0
        assert reconciled_net == 140.0  # 200 - 60

    def test_financing_fee_excluded(self, payment_with_financing_fee):
        """True financing_fee (name=="financing_fee") excluded from comissao."""
        mp_fee, _, _, reconciled_net, _ = _extract_processor_charges(
            payment_with_financing_fee
        )
        # financing_fee=30 excluded, only processing=10 + sale=40 = 50
        assert mp_fee == 50.0
        assert reconciled_net == 450.0  # 500 - 50

    def test_empty_charges(self, payment_no_charges):
        """Payment without charges_details → fees=0, shipping=0."""
        mp_fee, shipping, ship_id, reconciled_net, net_diff = _extract_processor_charges(
            payment_no_charges
        )
        assert mp_fee == 0.0
        assert shipping == 0.0
        assert ship_id is None
        # reconciled_net = amount - 0 - 0 = amount
        assert reconciled_net == 1172.33
        # net_diff = actual_net - reconciled_net (will be negative because real net < amount)
        assert net_diff == pytest.approx(900.0 - 1172.33, abs=0.01)

    def test_shipping_with_buyer_contribution(self):
        """When buyer pays part of shipping, seller cost = collector charges - buyer amount."""
        payment = {
            "transaction_amount": 300.0,
            "shipping_amount": 15.0,  # Buyer pays R$15
            "transaction_details": {"net_received_amount": 240.0},
            "charges_details": [
                {
                    "accounts": {"from": "collector", "to": "carrier"},
                    "amounts": {"original": 35.0, "refunded": 0},
                    "name": "shp_fulfillment",
                    "type": "shipping",
                    "metadata": {"shipment_id": 12345},
                },
                {
                    "accounts": {"from": "collector", "to": "mp"},
                    "amounts": {"original": 10.0, "refunded": 0},
                    "name": "mp_processing_fee",
                    "type": "fee",
                },
            ],
        }
        mp_fee, shipping, ship_id, reconciled_net, _ = _extract_processor_charges(payment)
        assert mp_fee == 10.0
        assert shipping == 20.0  # max(0, 35 - 15) = 20
        assert ship_id == "12345"
        assert reconciled_net == 270.0  # 300 - 10 - 20

    def test_rounding(self):
        """Verify proper rounding to 2 decimal places."""
        payment = {
            "transaction_amount": 100.0,
            "shipping_amount": 0,
            "transaction_details": {"net_received_amount": 85.57},
            "charges_details": [
                {
                    "accounts": {"from": "collector", "to": "mp"},
                    "amounts": {"original": 3.333, "refunded": 0},
                    "name": "fee1",
                    "type": "fee",
                },
                {
                    "accounts": {"from": "collector", "to": "ml"},
                    "amounts": {"original": 11.097, "refunded": 0},
                    "name": "fee2",
                    "type": "fee",
                },
            ],
        }
        mp_fee, shipping, _, _, _ = _extract_processor_charges(payment)
        # 3.333 + 11.097 = 14.43 (rounded)
        assert mp_fee == 14.43
        assert shipping == 0.0


# ===========================================================================
# _compute_effective_net_amount
# ===========================================================================

class TestComputeEffectiveNetAmount:
    def test_normal_approved(self, approved_payment_fees_only):
        """Non-partially-refunded → returns net_received_amount as-is."""
        result = _compute_effective_net_amount(approved_payment_fees_only)
        assert result == 45.68

    def test_partially_refunded(self):
        """Partially refunded → adjusts net by refunded amount minus refunded charges."""
        payment = {
            "status_detail": "partially_refunded",
            "transaction_amount": 200.0,
            "transaction_amount_refunded": 50.0,
            "transaction_details": {"net_received_amount": 160.0},
            "charges_details": [
                {
                    "accounts": {"from": "collector", "to": "mp"},
                    "amounts": {"original": 20.0, "refunded": 5.0},
                    "name": "mp_processing_fee",
                    "type": "fee",
                },
                {
                    "accounts": {"from": "collector", "to": "carrier"},
                    "amounts": {"original": 20.0, "refunded": 10.0},
                    "name": "shp_fulfillment",
                    "type": "shipping",
                },
            ],
            "refunds": [],
        }
        result = _compute_effective_net_amount(payment)
        # net=160 - max(0, refunded_amount(50) - refunded_charges(5+10=15)) = 160 - 35 = 125
        assert result == 125.0

    def test_partially_refunded_no_refund_amount(self):
        """Uses refunds array when transaction_amount_refunded is 0."""
        payment = {
            "status_detail": "partially_refunded",
            "transaction_amount": 200.0,
            "transaction_amount_refunded": 0,
            "transaction_details": {"net_received_amount": 160.0},
            "charges_details": [],
            "refunds": [
                {"amount": 30.0},
                {"amount": 20.0},
            ],
        }
        result = _compute_effective_net_amount(payment)
        # net=160 - max(0, sum_refunds(50) - refunded_charges(0)) = 160 - 50 = 110
        assert result == 110.0

    def test_financing_fee_excluded_from_partial(self):
        """financing_fee charges are excluded from refunded_charges sum."""
        payment = {
            "status_detail": "partially_refunded",
            "transaction_amount": 300.0,
            "transaction_amount_refunded": 100.0,
            "transaction_details": {"net_received_amount": 250.0},
            "charges_details": [
                {
                    "accounts": {"from": "collector", "to": "mp"},
                    "amounts": {"original": 30.0, "refunded": 30.0},
                    "name": "financing_fee",
                    "type": "fee",
                },
                {
                    "accounts": {"from": "collector", "to": "mp"},
                    "amounts": {"original": 10.0, "refunded": 5.0},
                    "name": "mp_processing_fee",
                    "type": "fee",
                },
            ],
            "refunds": [],
        }
        result = _compute_effective_net_amount(payment)
        # financing_fee refunded=30 is EXCLUDED
        # refunded_charges = 5 only (processing_fee)
        # adjusted = 250 - max(0, 100 - 5) = 250 - 95 = 155
        assert result == 155.0


# ===========================================================================
# Refund charge extraction (simulates _process_refunded logic)
# ===========================================================================

class TestRefundChargeExtraction:
    """Tests the granular refund logic from _process_refunded.

    The old code used blanket (amount - net) for estorno_taxa.
    The fix separates refunded_fee from refunded_shipping.
    """

    def _extract_refunded_charges(self, payment: dict) -> tuple[float, float]:
        """Replicate the refund charge extraction from _process_refunded."""
        refunded_fee = 0.0
        refunded_shipping = 0.0
        charges = payment.get("charges_details") or []

        for charge in charges:
            accounts = charge.get("accounts") or {}
            if accounts.get("from") != "collector":
                continue

            charge_type = str(charge.get("type") or "").lower()
            charge_name = str(charge.get("name") or "").strip().lower()

            if charge_name == "financing_fee":
                continue

            refunded_val = _to_float((charge.get("amounts") or {}).get("refunded"))
            if charge_type == "fee":
                refunded_fee += refunded_val
            elif charge_type == "shipping":
                refunded_shipping += refunded_val

        return round(refunded_fee, 2), round(refunded_shipping, 2)

    def test_full_refund_all_charges_refunded(self, refunded_payment_full):
        """Full refund: all charges refunded."""
        fee, shipping = self._extract_refunded_charges(refunded_payment_full)
        # mp_financing_fee (123.98) + mp_processing_fee (29.16) + ml_sale_fee (71.03) = 224.17
        assert fee == 224.17
        assert shipping == 85.95

    def test_partial_refund_shipping_retained(self, refunded_payment_partial_shipping):
        """TIPO 4 bug: fee refunded but shipping NOT refunded."""
        fee, shipping = self._extract_refunded_charges(refunded_payment_partial_shipping)
        # Only fees were refunded: 10.50 + 14.55 = 25.05
        assert fee == 25.05
        # Shipping was NOT refunded
        assert shipping == 0.0

    def test_no_charges_fallback(self, payment_no_charges):
        """No charges_details → both zero (caller uses fallback)."""
        fee, shipping = self._extract_refunded_charges(payment_no_charges)
        assert fee == 0.0
        assert shipping == 0.0


# ===========================================================================
# Payload builders
# ===========================================================================

class TestBuildParcela:
    def test_basic_parcela(self):
        parcela = _build_parcela("Venda ML #123", "2026-01-15", "conta-uuid", 100.0)
        assert parcela["descricao"] == "Venda ML #123"
        assert parcela["data_vencimento"] == "2026-01-15"
        assert parcela["conta_financeira"] == "conta-uuid"
        assert parcela["detalhe_valor"]["valor_bruto"] == 100.0
        assert parcela["detalhe_valor"]["valor_liquido"] == 100.0

    def test_parcela_with_nota(self):
        parcela = _build_parcela("desc", "2026-01-01", "conta", 50.0, "custom note")
        assert parcela["nota"] == "custom note"

    def test_parcela_default_nota(self):
        parcela = _build_parcela("desc", "2026-01-01", "conta", 50.0)
        assert parcela["nota"] == "desc"  # defaults to descricao


class TestBuildEvento:
    def test_basic_evento_structure(self):
        parcela = _build_parcela("test", "2026-01-15", "conta", 100.0)
        evento = _build_evento(
            "2026-01-01", 100.0, "desc", "obs", "contato-uuid",
            "conta-uuid", "cat-uuid", "cc-uuid", parcela,
        )
        assert evento["data_competencia"] == "2026-01-01"
        assert evento["valor"] == 100.0
        assert evento["descricao"] == "desc"
        assert evento["observacao"] == "obs"
        assert evento["contato"] == "contato-uuid"
        assert evento["conta_financeira"] == "conta-uuid"
        assert len(evento["rateio"]) == 1
        assert evento["rateio"][0]["id_categoria"] == "cat-uuid"
        assert evento["rateio"][0]["valor"] == 100.0
        assert evento["rateio"][0]["rateio_centro_custo"][0]["id_centro_custo"] == "cc-uuid"
        assert evento["condicao_pagamento"]["parcelas"] == [parcela]

    def test_evento_without_centro_custo(self):
        parcela = _build_parcela("test", "2026-01-15", "conta", 100.0)
        evento = _build_evento(
            "2026-01-01", 100.0, "desc", "obs", "contato",
            "conta", "cat", None, parcela,
        )
        assert "rateio_centro_custo" not in evento["rateio"][0]


class TestBuildDespesaPayload:
    def test_despesa_uses_seller_config(self, seller_config):
        payload = _build_despesa_payload(
            seller_config, "2026-01-01", "2026-01-15", 50.0,
            "Comissão ML - Payment 123", "Venda #456",
            CA_CATEGORIES["comissao_ml"],
        )
        assert payload["data_competencia"] == "2026-01-01"
        assert payload["valor"] == 50.0
        assert payload["conta_financeira"] == "test-conta-uuid"
        assert payload["contato"] == "test-contato-uuid"
        assert payload["rateio"][0]["id_categoria"] == CA_CATEGORIES["comissao_ml"]
        parcela = payload["condicao_pagamento"]["parcelas"][0]
        assert parcela["data_vencimento"] == "2026-01-15"


# ===========================================================================
# End-to-end: payment → expected CA values
# ===========================================================================

class TestPaymentToCAValues:
    """Integration-style tests that verify the complete calculation chain.

    Given a real payment, verify that the extracted values match what
    should go into Conta Azul.
    """

    def test_approved_simple_fees(self, approved_payment_fees_only, seller_config):
        """Simple approved payment → receita=66.30, comissao=20.62, frete=0."""
        payment = approved_payment_fees_only
        mp_fee, shipping, _, reconciled_net, _ = _extract_processor_charges(payment)

        # Receita = transaction_amount
        assert payment["transaction_amount"] == 66.3
        # Comissao
        assert mp_fee == 20.62
        # Frete
        assert shipping == 0.0
        # Subsidy: net_real - reconciled_net
        net_real = payment["transaction_details"]["net_received_amount"]
        subsidy = round(net_real - reconciled_net, 2)
        assert subsidy == 0.0  # No subsidy

    def test_approved_with_shipping(self, approved_payment_with_shipping, seller_config):
        """Approved with shipping → receita=260.20, comissao=44.23, frete=23.45."""
        payment = approved_payment_with_shipping
        mp_fee, shipping, _, reconciled_net, _ = _extract_processor_charges(payment)

        assert payment["transaction_amount"] == 260.2
        assert mp_fee == 44.23
        assert shipping == 23.45
        net_real = payment["transaction_details"]["net_received_amount"]
        subsidy = round(net_real - reconciled_net, 2)
        assert subsidy == 0.0

    def test_competencia_date(self, approved_payment_fees_only):
        """Competencia uses date_approved converted to BRT."""
        competencia = _to_brt_date(approved_payment_fees_only["date_approved"])
        assert competencia == "2026-01-01"

    def test_vencimento_date(self, approved_payment_fees_only):
        """Vencimento uses money_release_date[:10]."""
        vencimento = approved_payment_fees_only["money_release_date"][:10]
        assert vencimento == "2026-01-17"
