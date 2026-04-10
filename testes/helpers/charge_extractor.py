"""T003 - Standalone charge extractor mirroring processor._extract_processor_charges.

This module replicates the fee/shipping extraction logic from processor.py
WITHOUT importing it, so it can be used in tests as an independent oracle.

Logic source: processor.py _extract_processor_charges (as of US-010 unification).
"""
from __future__ import annotations


def extract_charges(payment: dict) -> tuple[float, float, float]:
    """Extract MP fee, seller shipping cost and calculated net from a payment dict.

    Replicates processor._extract_processor_charges exactly:
    - Iterates charges_details
    - Sums fee charges (from=collector, excluding financing_fee by name)
    - Also sums coupon-type charges (from=collector) into mp_fee
    - Sums shipping charges (from=collector)
    - shipping_cost_seller = max(0, shipping_charges_collector - shipping_amount_buyer)
    - calculated_net = transaction_amount - mp_fee - shipping_cost_seller

    Args:
        payment: Raw payment dict as returned by the ML Payments API.

    Returns:
        Tuple of (mp_fee, shipping_cost_seller, calculated_net), all rounded
        to 2 decimal places.
    """
    charges: list[dict] = payment.get("charges_details") or []
    amount: float = float(payment.get("transaction_amount") or 0)

    mp_fee: float = 0.0
    shipping_charges_collector: float = 0.0

    for charge in charges:
        accounts = charge.get("accounts", {}) or {}
        if accounts.get("from") != "collector":
            continue

        charge_amount = float((charge.get("amounts", {}) or {}).get("original") or 0)
        charge_type = charge.get("type")

        if charge_type == "shipping":
            shipping_charges_collector += charge_amount
        elif charge_type == "fee":
            charge_name = (charge.get("name") or "").strip().lower()
            if charge_name == "financing_fee":
                continue
            mp_fee += charge_amount
        elif charge_type == "coupon":
            mp_fee += charge_amount

    shipping_cost_seller = round(
        max(0.0, shipping_charges_collector - float(payment.get("shipping_amount") or 0)),
        2,
    )
    mp_fee = round(mp_fee, 2)
    calculated_net = round(amount - mp_fee - shipping_cost_seller, 2)

    return mp_fee, shipping_cost_seller, calculated_net
