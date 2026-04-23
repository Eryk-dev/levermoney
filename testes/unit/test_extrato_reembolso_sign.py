"""ERR-0024/0025 regression tests — sign-driven for refund/cancellation types.

When the CSV line is a cancellation or refund, the direction can go either
way. Hardcoded `income` direction (from the classification rule) flips the
sign for reversals like "Cancelamento do reembolso do frete" (-0,89) or
"Reembolso de pagamento de conta" (+2168,75, reverses an outgoing payment).
"""
from __future__ import annotations

import asyncio

import pytest

from app.services import extrato_ingester


pytestmark = pytest.mark.architecture


def _mk_fakes(monkeypatch):
    written: list[dict] = []

    async def _fake_write(seller_slug, payment_id_key, expense_type, direction,
                          ca_category_uuid, tx, description):
        written.append({
            "key": payment_id_key,
            "type": expense_type,
            "direction": direction,
            "amount": tx.get("amount"),
        })

    async def _noop_async_set(*a, **kw):
        return set()

    def _noop_set(*a, **kw):
        return set()

    class _FakeDB:
        def table(self, *a, **kw):
            raise AssertionError("DB not expected")

    monkeypatch.setattr(extrato_ingester, "_write_extrato_expense_events", _fake_write)
    monkeypatch.setattr(extrato_ingester, "_batch_lookup_payment_ids", _noop_async_set)
    monkeypatch.setattr(extrato_ingester, "_batch_lookup_refunded_payment_ids", _noop_async_set)
    monkeypatch.setattr(extrato_ingester, "_batch_lookup_expense_payment_ids", _noop_set)
    monkeypatch.setattr(extrato_ingester, "_batch_lookup_composite_expense_ids", _noop_set)
    monkeypatch.setattr(extrato_ingester, "_batch_lookup_max_suffix_per_base", lambda db, seller, keys: {k: 0 for k in keys})
    monkeypatch.setattr(extrato_ingester, "get_db", lambda: _FakeDB())
    monkeypatch.setattr(
        extrato_ingester,
        "get_seller_config",
        lambda db, slug: {"slug": slug, "id": "stub"},
    )
    return written


def test_err_0025_cancelamento_reembolso_frete_is_expense(monkeypatch):
    """Negative CSV for 'Cancelamento do reembolso do frete' → expense."""
    written = _mk_fakes(monkeypatch)
    csv = """\
INITIAL_BALANCE;CREDITS;DEBITS;FINAL_BALANCE
0,00;0,00;-0,89;-0,89

RELEASE_DATE;TRANSACTION_TYPE;REFERENCE_ID;TRANSACTION_NET_AMOUNT;PARTIAL_BALANCE
20-01-2026;Cancelamento do reembolso do frete Mercado Envios;138188309762;-0,89;-0,89
"""
    asyncio.run(extrato_ingester.ingest_extrato_from_csv("X", csv, "2026-01"))
    assert len(written) == 1
    assert written[0]["type"] == "reembolso_generico"
    assert written[0]["direction"] == "expense"


def test_err_0025_reembolso_pagamento_conta_is_income(monkeypatch):
    """Positive CSV for 'Reembolso de pagamento de conta' → income."""
    written = _mk_fakes(monkeypatch)
    csv = """\
INITIAL_BALANCE;CREDITS;DEBITS;FINAL_BALANCE
0,00;2168,75;0,00;2168,75

RELEASE_DATE;TRANSACTION_TYPE;REFERENCE_ID;TRANSACTION_NET_AMOUNT;PARTIAL_BALANCE
30-01-2026;Reembolso de pagamento de conta Banco Safra S.A.;143418013753;2168,75;2168,75
"""
    asyncio.run(extrato_ingester.ingest_extrato_from_csv("X", csv, "2026-01"))
    assert len(written) == 1
    assert written[0]["type"] == "pagamento_conta"
    assert written[0]["direction"] == "income"
