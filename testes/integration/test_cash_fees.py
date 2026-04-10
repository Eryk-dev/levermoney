"""Phase 8 - Fee validation tests (T029-T031).

T029: Compare processor-extracted fees vs ML API net_received_amount.
T030: Verify fee+shipping breakdown is internally consistent per payment.
T031: Verify release_report_validator is wired into the nightly pipeline.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

pytest_plugins = ["testes.integration.conftest_cash"]

from testes.helpers.charge_extractor import extract_charges  # noqa: E402
from app.services.processor import _to_float  # noqa: E402

# Tolerance in BRL below which a divergence is ignored.
_DIVERGENCE_THRESHOLD = 0.01


class TestFeeValidation:
    """T029 / T030 - Fee extraction accuracy against ML API ground truth."""

    # ------------------------------------------------------------------
    # T029
    # ------------------------------------------------------------------

    def test_processor_fees_vs_release_report(
        self,
        ml_payments_jan: list[dict],
    ) -> None:
        """T029: processor-extracted net must match ML net_received_amount.

        For every approved sale payment in January that has a non-null
        net_received_amount the test computes:

            calculated_net = transaction_amount - mp_fee - shipping_cost

        and compares it against the ML API's own ``net_received_amount``.
        Divergences >= R$0.01 are collected and reported as a single
        assertion failure so the whole batch is always evaluated.
        """
        approved_payments = [
            p
            for p in ml_payments_jan
            if p.get("status") == "approved"
            and p.get("transaction_details", {}).get("net_received_amount") is not None
        ]

        assert approved_payments, (
            "No approved payments found in January fixture - "
            "check ML API credentials and date range."
        )

        divergences: list[str] = []

        for payment in approved_payments:
            payment_id = payment.get("id")
            transaction_amount = _to_float(payment.get("transaction_amount"))
            net_received = _to_float(
                payment["transaction_details"]["net_received_amount"]
            )

            _mp_fee, _shipping_cost, calculated_net = extract_charges(payment)

            # Skip ML subsidy payments where net > amount (platform adds bonus)
            if net_received > transaction_amount:
                continue

            diff = abs(calculated_net - net_received)
            if diff >= _DIVERGENCE_THRESHOLD:
                divergences.append(
                    f"payment_id={payment_id} "
                    f"transaction_amount={transaction_amount:.2f} "
                    f"mp_fee={_mp_fee:.2f} "
                    f"shipping_cost={_shipping_cost:.2f} "
                    f"calculated_net={calculated_net:.2f} "
                    f"ml_net_received={net_received:.2f} "
                    f"diff={diff:.4f}"
                )

        total = len(approved_payments)
        divergent = len(divergences)
        summary = (
            f"{divergent}/{total} approved payments have net divergence "
            f">= R${_DIVERGENCE_THRESHOLD:.2f}:\n"
            + "\n".join(divergences)
        )

        assert not divergences, summary

    # ------------------------------------------------------------------
    # T030
    # ------------------------------------------------------------------

    def test_fee_shipping_breakdown_consistent(
        self,
        ml_payments_jan: list[dict],
    ) -> None:
        """T030: fee+shipping breakdown must reconcile to net_received_amount.

        For every approved payment in January, verifies the identity:

            transaction_amount - mp_fee - shipping_cost ≈ net_received_amount

        This is effectively the same identity as T029 but framed as an
        internal-consistency check: if ``extract_charges`` is correct AND
        ML's net figure is reliable, both sides must agree within R$0.01.

        Collects all failures before asserting so the full list is visible
        in a single run.
        """
        approved_payments = [
            p
            for p in ml_payments_jan
            if p.get("status") == "approved"
            and p.get("transaction_details", {}).get("net_received_amount") is not None
        ]

        assert approved_payments, (
            "No approved payments found in January fixture - "
            "check ML API credentials and date range."
        )

        inconsistencies: list[str] = []

        for payment in approved_payments:
            payment_id = payment.get("id")
            transaction_amount = _to_float(payment.get("transaction_amount"))
            net_received = _to_float(
                payment["transaction_details"]["net_received_amount"]
            )

            mp_fee, shipping_cost, calculated_net = extract_charges(payment)

            # Recompute explicitly to make the identity visible in the test.
            recomputed = round(transaction_amount - mp_fee - shipping_cost, 2)
            diff_internal = abs(recomputed - calculated_net)
            diff_vs_ml = abs(recomputed - net_received)

            # Internal consistency: recomputed value must equal calculated_net.
            if diff_internal >= _DIVERGENCE_THRESHOLD:
                inconsistencies.append(
                    f"payment_id={payment_id} INTERNAL MISMATCH "
                    f"transaction_amount={transaction_amount:.2f} "
                    f"mp_fee={mp_fee:.2f} "
                    f"shipping_cost={shipping_cost:.2f} "
                    f"recomputed={recomputed:.2f} "
                    f"calculated_net={calculated_net:.2f} "
                    f"diff={diff_internal:.4f}"
                )

            # Cross-validation against ML API net (skip subsidies where net > amount).
            if net_received > transaction_amount:
                continue
            if diff_vs_ml >= _DIVERGENCE_THRESHOLD:
                inconsistencies.append(
                    f"payment_id={payment_id} VS_ML_NET MISMATCH "
                    f"transaction_amount={transaction_amount:.2f} "
                    f"mp_fee={mp_fee:.2f} "
                    f"shipping_cost={shipping_cost:.2f} "
                    f"recomputed={recomputed:.2f} "
                    f"ml_net_received={net_received:.2f} "
                    f"diff={diff_vs_ml:.4f}"
                )

        total = len(approved_payments)
        failed = len(inconsistencies)
        summary = (
            f"{failed} inconsistency/ies found across {total} approved payments:\n"
            + "\n".join(inconsistencies)
        )

        assert not inconsistencies, summary


# ---------------------------------------------------------------------------
# T031
# ---------------------------------------------------------------------------


def test_release_report_validator_in_pipeline() -> None:
    """T031: verify release_report_validator runs in the nightly pipeline.

    Reads app/main.py as plain text and asserts that either
    ``validate_release_fees`` or ``release_report_validator`` appears,
    confirming the validator is wired into the lifespan/pipeline logic.
    """
    main_source = (PROJECT_ROOT / "app" / "main.py").read_text()
    assert "validate_release_fees" in main_source or "release_report_validator" in main_source, (
        "Neither 'validate_release_fees' nor 'release_report_validator' found in "
        "app/main.py. The nightly pipeline may not be running fee validation."
    )
