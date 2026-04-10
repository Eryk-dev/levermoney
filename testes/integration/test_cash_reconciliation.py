"""
Cash Reconciliation Tests — validates daily cash totals match ML extrato.

Uses LIVE ML API data (session-cached) and real extrato CSVs.
Run: python3 -m pytest testes/integration/test_cash_reconciliation.py -v
"""
import pytest
from collections import defaultdict
from pathlib import Path

# Import helpers
from testes.helpers.extrato_parser import parse_extrato_csv, parse_br_number
from testes.helpers.charge_extractor import extract_charges

# Import processor functions for comparison
from app.services.processor import _to_brt_date, _to_float

# Activate session fixtures from conftest_cash
pytest_plugins = ["testes.integration.conftest_cash"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _is_valid_sale_payment(payment: dict) -> bool:
    """Return True if payment should be included in net validation.

    Applies the same filters as processor.py:
    - status must be "approved"
    - must have an order_id
    - skip marketplace_shipment description
    - skip payments where collector.id is present (those are purchases)
    """
    if payment.get("status") != "approved":
        return False
    if not payment.get("order", {}) and not payment.get("order_id"):
        return False
    if (payment.get("description") or "").strip() == "marketplace_shipment":
        return False
    if payment.get("collector", {}) and payment["collector"].get("id"):
        return False
    return True


def _normalize(text: str) -> str:
    """Remove accents for matching (e.g. 'Liberação' -> 'liberacao')."""
    import unicodedata
    nfkd = unicodedata.normalize("NFKD", text)
    return "".join(c for c in nfkd if not unicodedata.combining(c)).lower()


def _find_liberacao(payment: dict, extrato_by_ref_id: dict[str, list[dict]]) -> dict | None:
    """Return the extrato liberacao line for a given payment, or None."""
    ref_id = str(payment["id"])
    lines = extrato_by_ref_id.get(ref_id, [])
    for line in lines:
        if "liberacao" in _normalize(line.get("transaction_type") or ""):
            return line
    return None


# ---------------------------------------------------------------------------
# Phase 3 — Per-Payment Net Validation
# ---------------------------------------------------------------------------

class TestPerPaymentNet:
    """T005-T008: Validate that calculated net (amount - fees - shipping) matches
    the extrato liberacao amount for each approved sale payment."""

    # -----------------------------------------------------------------------
    # T005
    # -----------------------------------------------------------------------

    def test_single_payment_net_matches_extrato(
        self,
        ml_payments_jan: list[dict],
        extrato_by_ref_id_jan: dict[str, list[dict]],
    ) -> None:
        """T005: Spot-check one approved payment's net against extrato.

        Finds the first approved sale payment that has a matching
        'Liberacao de dinheiro' line and asserts the difference is < R$0.02.
        """
        sample_payment = None
        sample_extrato_line = None

        for payment in ml_payments_jan:
            if not _is_valid_sale_payment(payment):
                continue
            line = _find_liberacao(payment, extrato_by_ref_id_jan)
            if line is not None:
                sample_payment = payment
                sample_extrato_line = line
                break

        assert sample_payment is not None, (
            "Could not find any approved sale payment with a matching extrato liberacao "
            "in January data. Check extrato CSV and ML API fixture."
        )

        mp_fee, shipping_cost_seller, calculated_net = extract_charges(sample_payment)
        extrato_amount = sample_extrato_line["amount"]
        diff = abs(calculated_net - extrato_amount)

        assert diff < 0.02, (
            f"Net mismatch for payment {sample_payment['id']}: "
            f"calculated_net={calculated_net:.2f}, extrato_amount={extrato_amount:.2f}, "
            f"diff={diff:.4f} | mp_fee={mp_fee:.2f}, shipping={shipping_cost_seller:.2f}, "
            f"transaction_amount={sample_payment.get('transaction_amount')}"
        )

    # -----------------------------------------------------------------------
    # T006
    # -----------------------------------------------------------------------

    def test_all_payments_net_match_extrato_jan(
        self,
        ml_payments_jan: list[dict],
        extrato_by_ref_id_jan: dict[str, list[dict]],
    ) -> None:
        """T006: All approved January payments with a liberacao must match net.

        Iterates every approved sale payment that has a matching extrato
        liberacao line, calculates net via extract_charges, and asserts the
        difference is < R$0.02 for each one.

        Reports counts and total divergence in the assertion message.
        """
        matches = 0
        mismatches: list[dict] = []
        skipped_no_extrato = 0
        total_divergence = 0.0

        for payment in ml_payments_jan:
            if not _is_valid_sale_payment(payment):
                continue

            line = _find_liberacao(payment, extrato_by_ref_id_jan)
            if line is None:
                skipped_no_extrato += 1
                continue

            mp_fee, shipping_cost_seller, calculated_net = extract_charges(payment)
            extrato_amount = line["amount"]
            diff = abs(calculated_net - extrato_amount)

            if diff < 0.02:
                matches += 1
            else:
                total_divergence += diff
                mismatches.append(
                    {
                        "payment_id": payment["id"],
                        "calculated_net": calculated_net,
                        "extrato_amount": extrato_amount,
                        "diff": diff,
                        "mp_fee": mp_fee,
                        "shipping": shipping_cost_seller,
                        "transaction_amount": payment.get("transaction_amount"),
                    }
                )

        mismatch_details = "\n".join(
            f"  payment_id={m['payment_id']}: calc={m['calculated_net']:.2f} "
            f"extrato={m['extrato_amount']:.2f} diff={m['diff']:.4f}"
            for m in mismatches[:20]  # cap to avoid flooding output
        )

        assert matches > 0, (
            f"No payment+extrato pairs found (skipped_no_extrato={skipped_no_extrato}). "
            "Check extrato reference_id matching and accent normalization."
        )
        assert len(mismatches) == 0, (
            f"January net mismatches: {len(mismatches)} payments diverged "
            f"(matches={matches}, skipped_no_extrato={skipped_no_extrato}, "
            f"total_divergence=R${total_divergence:.2f})\n"
            f"First mismatches:\n{mismatch_details}"
        )

    # -----------------------------------------------------------------------
    # T007
    # -----------------------------------------------------------------------

    def test_financing_fee_excluded(
        self,
        ml_payments_jan: list[dict],
        extrato_by_ref_id_jan: dict[str, list[dict]],
    ) -> None:
        """T007: financing_fee charges must be excluded from mp_fee.

        Finds a payment that contains a 'financing_fee' charge and verifies:
        1. The charge is NOT counted in mp_fee.
        2. The resulting net still matches the extrato liberacao amount.

        If no such payment exists in January, the test is skipped with a
        descriptive message (financing_fee payments are uncommon).
        """
        financing_payment = None
        extrato_line = None

        for payment in ml_payments_jan:
            if not _is_valid_sale_payment(payment):
                continue

            charges = payment.get("charges_details") or []
            has_financing = any(
                (c.get("name") or "").strip().lower() == "financing_fee"
                and (c.get("accounts", {}) or {}).get("from") == "collector"
                for c in charges
            )
            if not has_financing:
                continue

            line = _find_liberacao(payment, extrato_by_ref_id_jan)
            if line is not None:
                financing_payment = payment
                extrato_line = line
                break

        if financing_payment is None:
            pytest.skip(
                "No approved sale payment with a 'financing_fee' charge AND a matching "
                "extrato liberacao found in January data. Cannot exercise T007."
            )

        charges = financing_payment.get("charges_details") or []
        financing_fee_total = sum(
            float((c.get("amounts", {}) or {}).get("original") or 0)
            for c in charges
            if (c.get("name") or "").strip().lower() == "financing_fee"
            and (c.get("accounts", {}) or {}).get("from") == "collector"
        )

        mp_fee, shipping_cost_seller, calculated_net = extract_charges(financing_payment)
        transaction_amount = float(financing_payment.get("transaction_amount") or 0)

        # The naive fee (including financing_fee) would be higher
        naive_fee = round(mp_fee + financing_fee_total, 2)
        naive_net = round(transaction_amount - naive_fee - shipping_cost_seller, 2)

        extrato_amount = extrato_line["amount"]
        diff_correct = abs(calculated_net - extrato_amount)
        diff_naive = abs(naive_net - extrato_amount)

        assert diff_correct < diff_naive or diff_correct < 0.02, (
            f"financing_fee exclusion check failed for payment {financing_payment['id']}: "
            f"calculated_net={calculated_net:.2f}, naive_net={naive_net:.2f}, "
            f"extrato_amount={extrato_amount:.2f}, financing_fee={financing_fee_total:.2f}"
        )

        assert diff_correct < 0.02, (
            f"Net mismatch after financing_fee exclusion for payment {financing_payment['id']}: "
            f"calculated_net={calculated_net:.2f}, extrato_amount={extrato_amount:.2f}, "
            f"diff={diff_correct:.4f}"
        )

    # -----------------------------------------------------------------------
    # T008
    # -----------------------------------------------------------------------

    def test_all_payments_net_match_extrato_fev(
        self,
        ml_payments_feb: list[dict],
        extrato_by_ref_id_feb: dict[str, list[dict]],
    ) -> None:
        """T008: All approved February payments with a liberacao must match net.

        Mirrors T006 but uses February fixtures. Iterates every approved sale
        payment that has a matching extrato liberacao line and asserts
        calculated net differs by less than R$0.02.
        """
        matches = 0
        mismatches: list[dict] = []
        skipped_no_extrato = 0
        total_divergence = 0.0

        for payment in ml_payments_feb:
            if not _is_valid_sale_payment(payment):
                continue

            line = _find_liberacao(payment, extrato_by_ref_id_feb)
            if line is None:
                skipped_no_extrato += 1
                continue

            mp_fee, shipping_cost_seller, calculated_net = extract_charges(payment)
            extrato_amount = line["amount"]
            diff = abs(calculated_net - extrato_amount)

            if diff < 0.02:
                matches += 1
            else:
                total_divergence += diff
                mismatches.append(
                    {
                        "payment_id": payment["id"],
                        "calculated_net": calculated_net,
                        "extrato_amount": extrato_amount,
                        "diff": diff,
                        "mp_fee": mp_fee,
                        "shipping": shipping_cost_seller,
                        "transaction_amount": payment.get("transaction_amount"),
                    }
                )

        mismatch_details = "\n".join(
            f"  payment_id={m['payment_id']}: calc={m['calculated_net']:.2f} "
            f"extrato={m['extrato_amount']:.2f} diff={m['diff']:.4f}"
            for m in mismatches[:20]
        )

        assert len(mismatches) == 0, (
            f"February net mismatches: {len(mismatches)} payments diverged "
            f"(matches={matches}, skipped_no_extrato={skipped_no_extrato}, "
            f"total_divergence=R${total_divergence:.2f})\n"
            f"First mismatches:\n{mismatch_details}"
        )


# Additional test classes will be added below
