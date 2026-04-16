"""T-013 (ERR-0004) — canonical category mapping across producers.

Two producers create rows that the reconciliation matcher must compare:

  • `extrato_ingester._classify_extrato_line` produces an `expense_type` from
    the extrato CSV TRANSACTION_TYPE column.
  • `expense_classifier._classify` produces an `expense_type` from ML/MP API
    payments (non-orders).

Both can write rows that conceptually represent the SAME cash event (e.g.
Pagamento de conta Itaú via boleto). For the matcher to pair them, both
producers must agree on a single canonical category — or, when they don't,
the reconciliation engine must translate to a shared canonical name.

This test locks the contract: every (extrato canonical type, mp_expense
type) pair listed below must produce the SAME category when fed through
the matcher's `_expense_type_to_category` translator.
"""
from __future__ import annotations

import pytest

from app.services.extrato_ingester import _classify_extrato_line
from app.services.reconciliation import _expense_type_to_category


pytestmark = pytest.mark.classifier


# ─── Pairs that must collapse to the same canonical category ─────────────


PAIRS = [
    # (extrato TRANSACTION_TYPE,                  mp_expense.expense_type)
    ("Pagamento de conta Itaú",                   "bill_payment"),
    ("Transferencia Pix recebida",                "transfer_intra"),
    # Future producers can be added here; the test will lock the contract.
]


@pytest.mark.parametrize("extrato_type, mp_expense_type", PAIRS)
def test_classifier_output_matches_storage_type(
    extrato_type: str,
    mp_expense_type: str,
) -> None:
    """Both producer outputs must collapse to the same canonical category."""
    extrato_canonical, _direction, _cat = _classify_extrato_line(extrato_type)
    extrato_canonical = _expense_type_to_category(extrato_canonical or "")
    mp_canonical = _expense_type_to_category(mp_expense_type)

    assert extrato_canonical == mp_canonical, (
        f"naming mismatch: extrato {extrato_type!r} → {extrato_canonical!r}, "
        f"mp_expense {mp_expense_type!r} → {mp_canonical!r}"
    )


def test_bill_payment_and_pagamento_conta_are_same_canonical() -> None:
    assert _expense_type_to_category("bill_payment") == _expense_type_to_category("pagamento_conta")


def test_translator_is_idempotent() -> None:
    """Translating the canonical name a second time must be a no-op."""
    for raw in ("bill_payment", "pagamento_conta", "deposit", "transfer_pix"):
        canonical = _expense_type_to_category(raw)
        assert _expense_type_to_category(canonical) == canonical, (
            f"_expense_type_to_category not idempotent for {raw!r}"
        )
