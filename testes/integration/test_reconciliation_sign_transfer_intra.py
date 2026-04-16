"""ERR-0005 — reconciliation must sign transfer_intra incoming by default.

Background: ERR-0001 fixed `_is_incoming_transfer` in `expense_classifier.py`
so new payments land in `payment_events` with the correct sign. But
`reconciliation.expenses_to_movements()` has its OWN sign rule, and it still
treats every `transfer_intra` row in `mp_expenses` as `signed = -amount`
(outgoing). For a real incoming pix of R$ 53.000 in 141air jan/2026, this
produces an orphan pair:

  • extrato line (`Transferência Pix recebida`): +R$ 53.000, category
    `transferencia_pix_in`.
  • mp_expenses row (`transfer_intra`, direction=transfer): reconciliation
    flips to −R$ 53.000 even though the canonical category is the same after
    translation.

The matcher's three passes all fail (sign opposite, |Δ| ≫ tolerance), so the
pair lands as orphan_extrato + orphan_system and blocks coverage from rising
past 69% on 141air jan/2026.

Fix contract: `expenses_to_movements` must sign based on the **canonical
category** (same one the matcher pairs against), defaulting ambiguous
transfers to incoming per the ERR-0001 lesson.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from app.services.reconciliation import (
    CashMovement,
    expenses_to_movements,
    extrato_to_movements,
    match_movements,
)


pytestmark = pytest.mark.money_sign


# ─── Fixtures ─────────────────────────────────────────────────────────────


def _mp_expense(
    payment_id: str,
    expense_type: str,
    direction: str,
    amount: float,
    date_approved: str = "2026-01-26",
) -> dict:
    return {
        "payment_id": payment_id,
        "expense_type": expense_type,
        "expense_direction": direction,
        "amount": amount,
        "date_approved": date_approved,
        "external_reference": None,
    }


def _extrato_tx(
    reference_id: str,
    transaction_type: str,
    amount: float,
    date: str = "2026-01-26",
) -> dict:
    return {
        "reference_id": reference_id,
        "transaction_type": transaction_type,
        "amount": amount,
        "date": date,
    }


# ─── Unit: expenses_to_movements sign contract ────────────────────────────


class TestExpensesToMovementsSign:
    """The reconciliation layer must match the classifier's ERR-0001 fix."""

    def test_transfer_intra_defaults_to_incoming(self):
        """Historic mp_expense row with no raw_payment must be signed +."""
        expenses = [_mp_expense("143624212572", "transfer_intra", "transfer", 53000)]
        movs = expenses_to_movements(expenses)
        assert len(movs) == 1
        assert movs[0].amount == Decimal("53000.00"), (
            f"transfer_intra should be signed POSITIVE (incoming) by default "
            f"per ERR-0001, got {movs[0].amount}"
        )

    def test_deposit_is_incoming(self):
        expenses = [_mp_expense("P1", "deposit", "transfer", 1000)]
        movs = expenses_to_movements(expenses)
        assert movs[0].amount == Decimal("1000.00")

    def test_transferencia_pix_in_is_incoming(self):
        expenses = [_mp_expense("P1", "transferencia_pix_in", "transfer", 500)]
        movs = expenses_to_movements(expenses)
        assert movs[0].amount == Decimal("500.00")

    def test_entrada_dinheiro_is_incoming(self):
        expenses = [_mp_expense("P1", "entrada_dinheiro", "transfer", 200)]
        movs = expenses_to_movements(expenses)
        assert movs[0].amount == Decimal("200.00")

    def test_transfer_pix_remains_outgoing(self):
        """Generic outgoing PIX must stay negative."""
        expenses = [_mp_expense("P1", "transfer_pix", "transfer", 300)]
        movs = expenses_to_movements(expenses)
        assert movs[0].amount == Decimal("-300.00")

    def test_pix_enviado_is_outgoing(self):
        expenses = [_mp_expense("P1", "pix_enviado", "transfer", 400)]
        movs = expenses_to_movements(expenses)
        assert movs[0].amount == Decimal("-400.00")

    def test_income_direction_is_positive(self):
        expenses = [_mp_expense("P1", "cashback", "income", 10)]
        movs = expenses_to_movements(expenses)
        assert movs[0].amount == Decimal("10.00")

    def test_expense_direction_is_negative(self):
        expenses = [_mp_expense("P1", "bill_payment", "expense", 700)]
        movs = expenses_to_movements(expenses)
        assert movs[0].amount == Decimal("-700.00")


# ─── Integration: matcher pairs the 141air R$ 53k case ────────────────────


class TestMatcherPairsR53k:
    def test_transfer_intra_incoming_matches_extrato_pix_in(self):
        """End-to-end reproduction of the remaining R$ 53k orphan pair."""
        extrato_tx = [
            _extrato_tx("143624212572", "Transferência Pix recebida EASY COMERCIO", 53000.0),
        ]
        mp_rows = [_mp_expense("143624212572", "transfer_intra", "transfer", 53000)]

        # `payment_ids` set is consulted by extrato_to_movements only to
        # route CHECK_PAYMENTS rules; transferencia_pix_in doesn't need it,
        # but we pass an empty set to exercise the real code path.
        ext_movs = extrato_to_movements(extrato_tx, payment_ids=set())
        sys_movs = expenses_to_movements(mp_rows)

        results = match_movements(ext_movs, sys_movs, tolerance=Decimal("0.01"))

        assert [r.status for r in results] == ["match"], (
            "Expected a single match; got "
            f"{[(r.status, r.extrato and r.extrato.amount, r.system and r.system.amount) for r in results]}"
        )
        matched = results[0]
        assert matched.extrato.amount == Decimal("53000.00")
        assert matched.system.amount == Decimal("53000.00")
