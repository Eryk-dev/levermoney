"""ERR-0023 — qr_pix_nao_sync / liberacao_nao_sync direction must follow CSV sign.

"Pagamento com QR Pix X" is ambiguous: can be an outgoing payment (seller
pays X → -amount) or a QR receipt (customer pays seller → +amount). The
hardcoded fallback direction='income' in `_resolve_check_payments` flips
the sign for outgoing payments, producing orphan sys_mov (+500) vs
orphan extrato (-500) with identical ref/amount/abs.

Fix: after resolving _CHECK_PAYMENTS to a *_nao_sync fallback, override
direction from the CSV sign (same pattern already used for
_SIGN_DRIVEN_EXPENSE_TYPES in the first-pass classification).
"""
from __future__ import annotations

import asyncio
from decimal import Decimal

import pytest

from app.services import extrato_ingester

pytestmark = pytest.mark.architecture


CSV_FIXTURE = """\
INITIAL_BALANCE;CREDITS;DEBITS;FINAL_BALANCE
0,00;0,00;-500,00;-500,00

RELEASE_DATE;TRANSACTION_TYPE;REFERENCE_ID;TRANSACTION_NET_AMOUNT;PARTIAL_BALANCE
06-01-2026;Pagamento com QR Pix YAPAY PAGAMENTOS ONLINE SA;140868298520;-500,00;-500,00
"""


def test_err_0023_qr_pix_outgoing_payment_is_expense(monkeypatch):
    """Outgoing QR pix payment (CSV negative) must write signed_amount < 0."""
    written: list[dict] = []

    async def _fake_write(seller_slug, payment_id_key, expense_type, direction,
                          ca_category_uuid, tx, description):
        written.append({
            "key": payment_id_key,
            "type": expense_type,
            "direction": direction,
            "amount": tx.get("amount"),
        })

    async def _fake_payment_ids(*a, **kw):
        return set()

    async def _fake_refunded(*a, **kw):
        return set()

    def _fake_expense(*a, **kw):
        return set()

    def _fake_composite(*a, **kw):
        return set()

    monkeypatch.setattr(extrato_ingester, "_write_extrato_expense_events", _fake_write)
    monkeypatch.setattr(extrato_ingester, "_batch_lookup_payment_ids", _fake_payment_ids)
    monkeypatch.setattr(extrato_ingester, "_batch_lookup_refunded_payment_ids", _fake_refunded)
    monkeypatch.setattr(extrato_ingester, "_batch_lookup_expense_payment_ids", _fake_expense)
    monkeypatch.setattr(extrato_ingester, "_batch_lookup_composite_expense_ids", _fake_composite)
    monkeypatch.setattr(extrato_ingester, "_batch_lookup_max_suffix_per_base", lambda db, seller, keys: {k: 0 for k in keys})

    class _FakeDB:
        def table(self, *a, **kw):
            raise AssertionError("DB not expected")

    monkeypatch.setattr(extrato_ingester, "get_db", lambda: _FakeDB())
    monkeypatch.setattr(
        extrato_ingester,
        "get_seller_config",
        lambda db, slug: {"slug": slug, "id": "stub"},
    )

    asyncio.run(
        extrato_ingester.ingest_extrato_from_csv("TESTSELLER", CSV_FIXTURE, "2026-01")
    )

    assert len(written) == 1, written
    row = written[0]
    assert row["type"] == "qr_pix_nao_sync", row
    assert row["direction"] == "expense", (
        f"outgoing QR pix (CSV -500) must be 'expense', got {row['direction']!r}"
    )
