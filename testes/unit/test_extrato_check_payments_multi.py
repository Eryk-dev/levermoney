"""ERR-0021 — _CHECK_PAYMENTS resolution loses _key_counter disambiguation.

When a ref_id has multiple sequential "Débito por dívida Reclamações" lines in
the extrato (all classified as _CHECK_PAYMENTS first, resolved later to
debito_divida_disputa fallback), the resolution pass reconstructs
`payment_id_key = f"{ref}:{abbrev}"` WITHOUT consulting `_key_counter`.

Consequence: lines 2/3/N collide on `{ref}:dd` with line 1 → ON CONFLICT
DO NOTHING silently drops them → missing rows in payment_events →
orphan_extrato in reconciliation.

Fix: after resolving to fallback_type, apply the same `_key_counter`
disambiguation used in the first pass.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from app.services import extrato_ingester

pytestmark = pytest.mark.architecture


CSV_FIXTURE = """\
INITIAL_BALANCE;CREDITS;DEBITS;FINAL_BALANCE
0,00;0,00;-1000,00;-1000,00

RELEASE_DATE;TRANSACTION_TYPE;REFERENCE_ID;TRANSACTION_NET_AMOUNT;PARTIAL_BALANCE
02-01-2026;Débito por dívida Reclamações no Mercado Livre;999888777;-12,82;-12,82
02-01-2026;Débito por dívida Reclamações no Mercado Livre;999888777;-56,02;-68,84
02-01-2026;Débito por dívida Reclamações no Mercado Livre;999888777;-118,07;-186,91
"""


def test_err_0021_check_payments_multi_gets_unique_keys(monkeypatch):
    """Three sequential dispute debits for the same ref must generate
    three distinct composite keys (:dd, :dd:2, :dd:3).

    Red: only :dd is emitted (other two collide on the same key).
    Green: all three keys are emitted.
    """
    writes: list[tuple[str, str]] = []

    async def _fake_write(seller_slug, payment_id_key, expense_type, direction,
                          ca_category_uuid, tx, description):
        writes.append((payment_id_key, expense_type))

    async def _fake_payment_ids(*args, **kwargs):
        return set()  # ref not in payments → trigger CHECK_PAYMENTS resolution

    async def _fake_refunded(*args, **kwargs):
        return set()

    def _fake_expense_ids(*args, **kwargs):
        return set()

    def _fake_composite(*args, **kwargs):
        return set()

    monkeypatch.setattr(extrato_ingester, "_write_extrato_expense_events", _fake_write)
    monkeypatch.setattr(extrato_ingester, "_batch_lookup_payment_ids", _fake_payment_ids)
    monkeypatch.setattr(extrato_ingester, "_batch_lookup_refunded_payment_ids", _fake_refunded)
    monkeypatch.setattr(extrato_ingester, "_batch_lookup_expense_payment_ids", _fake_expense_ids)
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

    result = asyncio.run(
        extrato_ingester.ingest_extrato_from_csv("TESTSELLER", CSV_FIXTURE, "2026-01")
    )

    assert result["newly_ingested"] == 3, f"expected 3 writes, got {result}"
    keys = sorted(k for k, _ in writes)
    assert keys == ["999888777:dd", "999888777:dd:2", "999888777:dd:3"], keys
    assert all(etype == "debito_divida_disputa" for _, etype in writes)
