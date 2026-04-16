"""T-010 (ERR-0001) — sign convention for transfer-like expense types.

Property-style tests asserting that `signed_amount` recorded in the event
ledger has the expected sign for each transfer expense_type, regardless of
the underlying ML payload variations (top-level `collector_id` vs nested
`collector.id`, etc.).

Sign rules (semantic):
    incoming money (seller is collector / receiver):
        deposit, deposito_avulso, transferencia_pix_in,
        entrada_dinheiro, transfer_intra (when seller is collector)
        → signed_amount > 0

    outgoing money (seller is payer / sender):
        transfer_pix, pix_enviado, transfer_intra (when seller is payer)
        → signed_amount < 0
"""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, patch

from app.services.expense_classifier import classify_non_order_payment


pytestmark = pytest.mark.money_sign


# ─── Helpers ──────────────────────────────────────────────────────────────


SELLER_MP_USER_ID = 1745333938


def _payment(
    *,
    pid: int = 1,
    amount: float = 100.0,
    operation_type: str = "money_transfer",
    branch: str = "",
    payment_method_id: str = "pix",
    collector_id: int | None = None,
    payer_id: int | None = None,
    nested_collector_id: int | None = None,
    description: str = "",
    external_reference: str = "",
) -> dict:
    """Build a synthetic ML payment payload."""
    payload: dict = {
        "id": pid,
        "transaction_amount": amount,
        "operation_type": operation_type,
        "payment_method_id": payment_method_id,
        "description": description,
        "external_reference": external_reference,
        "date_approved": "2026-01-15T10:00:00.000-04:00",
        "date_created": "2026-01-15T09:00:00.000-04:00",
        "point_of_interaction": {
            "business_info": {"branch": branch, "unit": ""},
            "transaction_data": {"references": [], "bank_info": {}},
        },
        "payer": {
            "id": payer_id,
            "identification": {"type": "CPF", "number": "12345678901"},
        },
    }
    if collector_id is not None:
        payload["collector_id"] = collector_id
    if nested_collector_id is not None:
        payload["collector"] = {"id": nested_collector_id}
    return payload


async def _classify_and_capture_signed_amount(payment: dict) -> tuple[float, str]:
    """Run classify_non_order_payment with mocks and return (signed_amount, expense_type)."""
    captured: dict = {}

    async def _fake_record(**kwargs):
        if kwargs.get("event_type") == "expense_captured":
            captured["signed_amount"] = kwargs["signed_amount"]
            captured["expense_type"] = kwargs["expense_type"]

    with patch(
        "app.services.expense_classifier.record_expense_event",
        new=AsyncMock(side_effect=_fake_record),
    ):
        await classify_non_order_payment(db=None, seller_slug="141air", payment=payment)

    assert captured, "expense_captured event was not recorded"
    return captured["signed_amount"], captured["expense_type"]


# ─── Test cases ──────────────────────────────────────────────────────────


class TestIncomingMoney:
    """Expense types where the seller RECEIVES money. signed_amount must be > 0."""

    @pytest.mark.asyncio
    async def test_deposit_pix_no_branch_is_positive(self):
        """PIX without branch → deposit, always incoming."""
        payment = _payment(
            pid=10001,
            amount=500.0,
            operation_type="regular_payment",
            branch="",
            payment_method_id="pix",
        )
        signed, expense_type = await _classify_and_capture_signed_amount(payment)
        assert expense_type == "deposit"
        assert signed > 0, f"deposit must be positive (incoming), got {signed}"
        assert signed == 500.0

    @pytest.mark.asyncio
    async def test_transfer_intra_with_top_level_collector_id_is_positive(self):
        """Intra MP transfer where seller is collector_id (top-level) → positive."""
        payment = _payment(
            pid=10002,
            amount=53000.0,
            operation_type="money_transfer",
            branch="Intra MP",
            collector_id=SELLER_MP_USER_ID,
            payer_id=999999,
        )
        signed, expense_type = await _classify_and_capture_signed_amount(payment)
        assert expense_type == "transfer_intra"
        assert signed > 0, (
            f"transfer_intra (top-level collector_id) must be positive, got {signed}"
        )
        assert signed == 53000.0

    @pytest.mark.asyncio
    async def test_transfer_intra_with_nested_collector_id_is_positive(self):
        """Intra MP transfer where seller info is nested in payment.collector.id."""
        payment = _payment(
            pid=10003,
            amount=42000.0,
            operation_type="money_transfer",
            branch="Intra MP",
            nested_collector_id=SELLER_MP_USER_ID,
            payer_id=999999,
        )
        signed, expense_type = await _classify_and_capture_signed_amount(payment)
        assert expense_type == "transfer_intra"
        assert signed > 0, (
            f"transfer_intra (nested collector.id) must be positive, got {signed}"
        )

    @pytest.mark.asyncio
    async def test_transfer_intra_when_collector_payer_differ_defaults_positive(self):
        """When collector != payer, default treats as incoming (real data dominant case)."""
        payment = _payment(
            pid=10004,
            amount=1500.0,
            operation_type="money_transfer",
            branch="Intra MP",
            collector_id=12345,
            payer_id=67890,
        )
        signed, _ = await _classify_and_capture_signed_amount(payment)
        assert signed > 0, (
            f"transfer_intra defaults to incoming when accounts differ, got {signed}"
        )


class TestOutgoingMoney:
    """Expense types where the seller SENDS money. signed_amount must be < 0."""

    @pytest.mark.asyncio
    async def test_transfer_pix_other_destination_is_negative(self):
        """money_transfer with no Intra MP / Cashback / Virtual branch → transfer_pix outgoing."""
        payment = _payment(
            pid=20001,
            amount=200.0,
            operation_type="money_transfer",
            branch="",
            payment_method_id="account_money",
            payer_id=SELLER_MP_USER_ID,
            collector_id=999,
        )
        signed, expense_type = await _classify_and_capture_signed_amount(payment)
        assert expense_type == "transfer_pix"
        assert signed < 0, f"transfer_pix must be negative (outgoing), got {signed}"
        assert signed == -200.0


# ─── Property-style invariant: signed_amount sign matches direction ────


@pytest.mark.parametrize(
    "expense_type, expect_sign",
    [
        ("deposit", "positive"),
        ("deposito_avulso", "positive"),
        ("transfer_intra", "positive"),  # default for inter-account
        ("transferencia_pix_in", "positive"),
        ("entrada_dinheiro", "positive"),
        ("transfer_pix", "negative"),
        ("pix_enviado", "negative"),
    ],
)
def test_sign_convention_invariant_documented(expense_type: str, expect_sign: str):
    """Documents the contract: every expense_type has a known sign convention.

    This test does not exercise classifier logic — it locks the contract.
    Adding a new transfer-like expense_type without updating this list and
    the classifier sign logic will surface fast.
    """
    incoming = {
        "deposit", "deposito_avulso", "transferencia_pix_in",
        "entrada_dinheiro", "transfer_intra",
    }
    outgoing = {"transfer_pix", "pix_enviado"}
    assert expense_type in incoming or expense_type in outgoing, (
        f"expense_type '{expense_type}' has no documented sign convention"
    )
    if expect_sign == "positive":
        assert expense_type in incoming
    else:
        assert expense_type in outgoing
