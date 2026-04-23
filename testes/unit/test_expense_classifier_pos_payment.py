"""ERR-0022 — pos_payment (presencial sale) must not be classified as expense.

A pos_payment is a Mercado Pago POS sale: the seller swipes a card and money
flows IN. The extrato shows the net liberation (liberacao) for the same ref.
Classifying the pos_payment as "other/expense" creates a spurious sys_mov
that orphan-mismatches the extrato liberacao (different category + amount).

Contract: pos_payment → direction='skip' so no expense_captured event is
written; the extrato line for the same ref is then free to be ingested as
liberacao_nao_sync by extrato_ingester (and matches the extrato directly).
"""
from __future__ import annotations

import pytest

from app.services.expense_classifier import _classify


pytestmark = pytest.mark.architecture


def test_err_0022_pos_payment_is_skip():
    payment = {
        "id": 140775052358,
        "operation_type": "pos_payment",
        "payment_method_id": "visa",
        "transaction_amount": 210.0,
        "external_reference": "Venda presencial",
        "description": "Outro - Venda presencial",
        "point_of_interaction": {},
    }
    expense_type, direction, ca_category, auto, desc = _classify(payment)

    assert direction == "skip", (
        f"pos_payment must be skipped, got direction={direction!r}"
    )
    assert expense_type == "pos_payment", (
        f"expense_type should tag the skip reason, got {expense_type!r}"
    )
