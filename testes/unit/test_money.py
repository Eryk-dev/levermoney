"""Tests for app.services.money — unified sign convention."""

import pytest

from app.services.money import signed_amount


@pytest.mark.parametrize(
    "direction, amount, expected",
    [
        # income — always positive
        ("income", 100.0, 100.0),
        ("income", -50.0, 50.0),
        ("income", 0.0, 0.0),
        # expense — always negative
        ("expense", 50.0, -50.0),
        ("expense", -30.0, -30.0),
        ("expense", 0.0, 0.0),
        # transfer_in — always positive
        ("transfer_in", 200.0, 200.0),
        ("transfer_in", -200.0, 200.0),
        ("transfer_in", 0.0, 0.0),
        # transfer_out — always negative
        ("transfer_out", 75.0, -75.0),
        ("transfer_out", -75.0, -75.0),
        ("transfer_out", 0.0, 0.0),
        # deposit — always positive
        ("deposit", 500.0, 500.0),
        ("deposit", -500.0, 500.0),
        ("deposit", 0.0, 0.0),
    ],
    ids=[
        "income_positive",
        "income_negative_input",
        "income_zero",
        "expense_positive",
        "expense_negative_input",
        "expense_zero",
        "transfer_in_positive",
        "transfer_in_negative_input",
        "transfer_in_zero",
        "transfer_out_positive",
        "transfer_out_negative_input",
        "transfer_out_zero",
        "deposit_positive",
        "deposit_negative_input",
        "deposit_zero",
    ],
)
def test_signed_amount(direction: str, amount: float, expected: float) -> None:
    assert signed_amount(direction, amount) == expected


def test_unknown_direction_raises() -> None:
    with pytest.raises(ValueError, match="Unknown direction 'unknown'"):
        signed_amount("unknown", 10.0)


def test_empty_direction_raises() -> None:
    with pytest.raises(ValueError, match="Unknown direction ''"):
        signed_amount("", 10.0)
