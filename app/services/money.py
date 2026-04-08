"""Unified sign convention for financial amounts.

Single authoritative function for converting direction + amount to signed amount.
Replaces 6+ independent _signed_amount implementations scattered across the codebase.
"""


def signed_amount(direction: str, amount: float) -> float:
    """Convert a financial direction and absolute amount to a signed amount.

    Args:
        direction: One of 'income', 'expense', 'transfer_in', 'transfer_out', 'deposit'.
        amount: The (possibly unsigned) amount.

    Returns:
        Positive for income/transfer_in/deposit, negative for expense/transfer_out.

    Raises:
        ValueError: If direction is not recognised.
    """
    if direction in ("income", "transfer_in", "deposit"):
        return abs(amount)
    if direction in ("expense", "transfer_out"):
        return -abs(amount)
    raise ValueError(
        f"Unknown direction '{direction}'. "
        "Expected one of: income, expense, transfer_in, transfer_out, deposit"
    )
