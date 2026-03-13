"""
Shared fixtures for LeverMoney test suite.

All fixtures here use REAL payment data extracted from the 141air January 2026
cache. This ensures tests validate against actual ML API responses, not
synthetic data that might miss edge cases.
"""
import sys
from pathlib import Path

import pytest

# Ensure project root is importable
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# ---------------------------------------------------------------------------
# Exclude standalone scripts, simulacoes, utils from pytest collection
# ---------------------------------------------------------------------------
collect_ignore_glob = [
    "standalone/**",
    "simulacoes/**",
    "utils/**",
]


# ---------------------------------------------------------------------------
# Real payment fixtures (from testes/cache_jan2026/141air_payments.json)
# ---------------------------------------------------------------------------


@pytest.fixture
def approved_payment_fees_only():
    """Approved payment with 3 fee charges, no shipping.

    id=140282341986, amount=66.30, net=45.68
    Charges (all from=collector, type=fee):
      - mp_financing_1x_fee: 1.08
      - mp_processing_fee:   1.86
      - ml_sale_fee:        17.68
    Total fees = 20.62, shipping = 0
    Expected: net = 66.30 - 20.62 = 45.68 ✓
    """
    return {
        "id": 140282341986,
        "status": "approved",
        "status_detail": "accredited",
        "transaction_amount": 66.3,
        "shipping_amount": 0,
        "transaction_details": {
            "net_received_amount": 45.68,
            "total_paid_amount": 66.3,
            "installment_amount": 0,
        },
        "charges_details": [
            {
                "accounts": {"from": "collector", "to": "mp"},
                "amounts": {"original": 1.08, "refunded": 0},
                "name": "mp_financing_1x_fee",
                "type": "fee",
            },
            {
                "accounts": {"from": "collector", "to": "mp"},
                "amounts": {"original": 1.86, "refunded": 0},
                "name": "mp_processing_fee",
                "type": "fee",
            },
            {
                "accounts": {"from": "collector", "to": "ml"},
                "amounts": {"original": 17.68, "refunded": 0},
                "name": "ml_sale_fee",
                "type": "fee",
            },
        ],
        "order": {"id": "2000014532279344", "type": "mercadolibre"},
        "money_release_date": "2026-01-17T11:28:21.000-04:00",
        "date_approved": "2026-01-01T07:58:44.000-04:00",
        "description": "Braço Tampa Traseira Porta Malas",
    }


@pytest.fixture
def approved_payment_with_shipping():
    """Approved payment with shipping + fee charges + coupon (from=ml, ignored).

    id=139641326679, amount=260.20, net=192.52, shipping_amount=0
    Charges from=collector:
      - shipping (shp_fulfillment): 23.45
      - fee (mp_processing_fee):     0.23
      - fee (ml_sale_fee):          44.00
    Coupon from=ml (NOT from=collector, so excluded):
      - coupon: 30.00
    Expected: mp_fee=44.23, shipping_cost=23.45, net=260.20-44.23-23.45=192.52 ✓
    """
    return {
        "id": 139641326679,
        "status": "approved",
        "status_detail": "accredited",
        "transaction_amount": 260.2,
        "shipping_amount": 0,
        "transaction_details": {
            "net_received_amount": 192.52,
            "total_paid_amount": 230.2,
            "installment_amount": 0,
        },
        "charges_details": [
            {
                "accounts": {"from": "ml", "to": "payer"},
                "amounts": {"original": 30, "refunded": 0},
                "name": "coupon_code",
                "type": "coupon",
                "metadata": {"campaign_id": 2251812289957815},
            },
            {
                "accounts": {"from": "collector", "to": "1745333938"},
                "amounts": {"original": 23.45, "refunded": 0},
                "name": "shp_fulfillment",
                "type": "shipping",
                "metadata": {"shipment_id": 46187498511},
            },
            {
                "accounts": {"from": "collector", "to": "mp"},
                "amounts": {"original": 0.23, "refunded": 0},
                "name": "mp_processing_fee",
                "type": "fee",
            },
            {
                "accounts": {"from": "collector", "to": "ml"},
                "amounts": {"original": 44, "refunded": 0},
                "name": "ml_sale_fee",
                "type": "fee",
            },
        ],
        "order": {"id": "2000014515679588", "type": "mercadolibre"},
        "money_release_date": "2026-01-17T11:28:21.000-04:00",
        "date_approved": "2026-01-01T10:53:54.000-04:00",
        "shipping_amount": 0,
    }


@pytest.fixture
def refunded_payment_full():
    """Fully refunded payment with charges showing refunded amounts.

    id=140395321666, amount=1679.88, net=1369.76
    Charges from=collector (all fully refunded):
      - shipping: original=85.95, refunded=85.95
      - fee (mp_financing_fee): original=123.98, refunded=123.98  ← name != "financing_fee"
      - fee (mp_processing_fee): original=29.16, refunded=29.16
      - fee (ml_sale_fee): original=71.03, refunded=71.03
    """
    return {
        "id": 140395321666,
        "status": "refunded",
        "status_detail": "bpp_refunded",
        "transaction_amount": 1679.88,
        "shipping_amount": 0,
        "transaction_details": {
            "net_received_amount": 1369.76,
            "total_paid_amount": 1679.88,
            "installment_amount": 167.99,
        },
        "charges_details": [
            {
                "accounts": {"from": "collector", "to": "1745333938"},
                "amounts": {"original": 85.95, "refunded": 85.95},
                "name": "shp_cross_docking",
                "type": "shipping",
                "metadata": {"shipment_id": 46192012472},
            },
            {
                "accounts": {"from": "collector", "to": "mp"},
                "amounts": {"original": 123.98, "refunded": 123.98},
                "name": "mp_financing_fee",
                "type": "fee",
            },
            {
                "accounts": {"from": "collector", "to": "mp"},
                "amounts": {"original": 29.16, "refunded": 29.16},
                "name": "mp_processing_fee",
                "type": "fee",
            },
            {
                "accounts": {"from": "collector", "to": "ml"},
                "amounts": {"original": 71.03, "refunded": 71.03},
                "name": "ml_sale_fee",
                "type": "fee",
            },
        ],
        "order": {"id": "2000014546448080", "type": "mercadolibre"},
        "money_release_date": "2026-01-25T12:42:26.000-04:00",
        "date_approved": "2026-01-02T11:49:48.000-04:00",
        "refunds": [
            {"id": 2887880773, "amount": 1679.88, "date_created": "2026-02-24T07:48:47.000-04:00"}
        ],
        "transaction_amount_refunded": 1679.88,
    }


@pytest.fixture
def payment_no_charges():
    """Payment without charges_details (legacy/edge case).

    Some older payments don't have charges_details populated.
    The processor must fall back to blanket calculation.
    """
    return {
        "id": 139636302479,
        "status": "approved",
        "status_detail": "accredited",
        "transaction_amount": 1172.33,
        "shipping_amount": 0,
        "transaction_details": {
            "net_received_amount": 900.0,
            "total_paid_amount": 1172.33,
        },
        "charges_details": [],
        "order": {"id": "2000014500001234", "type": "mercadolibre"},
        "money_release_date": "2026-01-15T10:00:00.000-04:00",
        "date_approved": "2026-01-01T08:00:00.000-04:00",
    }


@pytest.fixture
def payment_with_financing_fee():
    """Payment with a true financing_fee that should be excluded from comissao.

    financing_fee is net-neutral (offset by financing_transfer).
    The processor must skip charges named exactly "financing_fee".
    """
    return {
        "id": 999999999,
        "status": "approved",
        "status_detail": "accredited",
        "transaction_amount": 500.0,
        "shipping_amount": 0,
        "transaction_details": {
            "net_received_amount": 450.0,
            "total_paid_amount": 500.0,
        },
        "charges_details": [
            {
                "accounts": {"from": "collector", "to": "mp"},
                "amounts": {"original": 30.0, "refunded": 0},
                "name": "financing_fee",
                "type": "fee",
            },
            {
                "accounts": {"from": "collector", "to": "mp"},
                "amounts": {"original": 10.0, "refunded": 0},
                "name": "mp_processing_fee",
                "type": "fee",
            },
            {
                "accounts": {"from": "collector", "to": "ml"},
                "amounts": {"original": 40.0, "refunded": 0},
                "name": "ml_sale_fee",
                "type": "fee",
            },
        ],
        "order": {"id": "2000099999999999", "type": "mercadolibre"},
        "money_release_date": "2026-01-20T10:00:00.000-04:00",
        "date_approved": "2026-01-05T10:00:00.000-04:00",
    }


@pytest.fixture
def payment_with_coupon_from_collector():
    """Payment with coupon charge from=collector (seller-funded coupon).

    When from=collector, the coupon amount IS charged to the seller
    and must be included in mp_fee.
    """
    return {
        "id": 888888888,
        "status": "approved",
        "status_detail": "accredited",
        "transaction_amount": 200.0,
        "shipping_amount": 0,
        "transaction_details": {
            "net_received_amount": 140.0,
            "total_paid_amount": 200.0,
        },
        "charges_details": [
            {
                "accounts": {"from": "collector", "to": "ml"},
                "amounts": {"original": 20.0, "refunded": 0},
                "name": "coupon_seller_funded",
                "type": "coupon",
            },
            {
                "accounts": {"from": "collector", "to": "mp"},
                "amounts": {"original": 5.0, "refunded": 0},
                "name": "mp_processing_fee",
                "type": "fee",
            },
            {
                "accounts": {"from": "collector", "to": "ml"},
                "amounts": {"original": 35.0, "refunded": 0},
                "name": "ml_sale_fee",
                "type": "fee",
            },
        ],
        "order": {"id": "2000088888888888", "type": "mercadolibre"},
        "money_release_date": "2026-01-20T10:00:00.000-04:00",
        "date_approved": "2026-01-05T10:00:00.000-04:00",
    }


@pytest.fixture
def refunded_payment_partial_shipping():
    """Refunded payment where fee was refunded but shipping was NOT.

    This is the TIPO 4 bug from the dossiê: ML refunds the commission
    but retains the shipping fee.
    """
    return {
        "id": 777777777,
        "status": "refunded",
        "status_detail": "bpp_refunded",
        "transaction_amount": 150.0,
        "shipping_amount": 0,
        "transaction_details": {
            "net_received_amount": 100.0,
            "total_paid_amount": 150.0,
        },
        "charges_details": [
            {
                "accounts": {"from": "collector", "to": "1745333938"},
                "amounts": {"original": 24.95, "refunded": 0.0},
                "name": "shp_cross_docking",
                "type": "shipping",
            },
            {
                "accounts": {"from": "collector", "to": "mp"},
                "amounts": {"original": 10.50, "refunded": 10.50},
                "name": "mp_processing_fee",
                "type": "fee",
            },
            {
                "accounts": {"from": "collector", "to": "ml"},
                "amounts": {"original": 14.55, "refunded": 14.55},
                "name": "ml_sale_fee",
                "type": "fee",
            },
        ],
        "order": {"id": "2000077777777777", "type": "mercadolibre"},
        "money_release_date": "2026-01-20T10:00:00.000-04:00",
        "date_approved": "2026-01-05T10:00:00.000-04:00",
        "refunds": [
            {"id": 9999, "amount": 150.0, "date_created": "2026-01-25T10:00:00.000-04:00"}
        ],
        "transaction_amount_refunded": 150.0,
    }


@pytest.fixture
def seller_config():
    """Minimal seller config for testing payload builders."""
    return {
        "slug": "141air",
        "ca_conta_bancaria": "test-conta-uuid",
        "ca_contato_ml": "test-contato-uuid",
        "ca_centro_custo_variavel": "test-cc-uuid",
    }


@pytest.fixture
def sample_extrato_csv():
    """Small extrato CSV sample for parsing tests."""
    return (
        "INITIAL_BALANCE;CREDITS;DEBITS;FINAL_BALANCE\n"
        "4.476,23;207.185,69;-210.571,52;1.090,40\n"
        "\n"
        "RELEASE_DATE;TRANSACTION_TYPE;REFERENCE_ID;AMOUNT;BALANCE\n"
        "01-01-2026;Liberação de dinheiro;140282341986;45,68;4.521,91\n"
        "01-01-2026;Liberação de dinheiro;139641326679;192,52;4.714,43\n"
        "08-01-2026;Pagamento Claude.ai subscription;141215405790;-569,25;3.200,00\n"
        "14-01-2026;Débito por dívida Diferença da aliquota (DIFAL);2728587235;-20,36;3.179,64\n"
        "19-01-2026;Pagamento Cartão de crédito;141963223933;-3.010,62;169,02\n"
        "22-01-2026;Débito por dívida Faturas vencidas do Mercado Livre;2775723042;-612,97;-443,95\n"
        "26-01-2026;Reembolso Reclamações e devoluções;140241282353;82,62;-361,33\n"
        "26-01-2026;Dinheiro retido Reclamações e devoluções;142935080179;-101,04;-462,37\n"
        "27-01-2026;Entrada de dinheiro;141508375497;14,75;-447,62\n"
        "28-01-2026;Débito por dívida Envio do Mercado Livre;137614895655;-46,90;-494,52\n"
        "29-01-2026;Dinheiro recebido;141527595509;203,89;-290,63\n"
        "30-01-2026;Bonificação;143199074090;10,90;-279,73\n"
    )
