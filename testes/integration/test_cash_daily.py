"""
Daily Cash Reconciliation Tests — T025-T027.

Validates that the processor's calculated net for each "Liberacao de dinheiro"
line matches the actual amount released by ML, grouped by calendar day (BRT).

For every day in the extrato:
    liberacao_computed = sum(extract_charges(payment)[2]
                             for each liberacao line whose reference_id
                             maps to a valid sale payment released that day)
    liberacao_extrato  = sum(extrato_line["amount"]
                             for each liberacao line on that day)
    divergence         = abs(liberacao_computed - liberacao_extrato)

T025 — January 2026: every day must have divergence < R$0.05
T026 — February 2026: every day must have divergence < R$0.05
T027 — Cumulative: sum of all per-day abs(divergence) across both months < R$5.00

Run:
    python3 -m pytest testes/integration/test_cash_daily.py -v
"""
from __future__ import annotations

from collections import defaultdict
from decimal import Decimal

import pytest

pytest_plugins = ["testes.integration.conftest_cash"]

from app.services.processor import _to_brt_date  # noqa: E402
from testes.helpers.charge_extractor import extract_charges  # noqa: E402

# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

DAILY_TOLERANCE = Decimal("0.05")
CUMULATIVE_TOLERANCE = Decimal("5.00")


def _normalize(text: str) -> str:
    """Remove accents for matching (e.g. 'Liberação' -> 'liberacao')."""
    import unicodedata
    nfkd = unicodedata.normalize("NFKD", text)
    return "".join(c for c in nfkd if not unicodedata.combining(c)).lower()


def _d(val: float | int | str) -> Decimal:
    """Convert any numeric value to Decimal."""
    return Decimal(str(val))


def _extrato_date_to_iso(dd_mm_yyyy: str) -> str:
    """Convert DD-MM-YYYY extrato date to YYYY-MM-DD ISO format.

    Args:
        dd_mm_yyyy: Date string in the format used by extrato CSVs.

    Returns:
        Date string in ISO 8601 format.
    """
    parts = dd_mm_yyyy.split("-")
    return f"{parts[2]}-{parts[1]}-{parts[0]}"


def _is_valid_sale(p: dict) -> bool:
    """Return True if payment would be processed as a sale by the processor.

    Applies the same filters as process_payment_webhook():
    - Must have an order with a non-None id
    - description must not be 'marketplace_shipment'
    - collector.id must be None (not a purchase on behalf of another seller)
    - status must not be 'cancelled' or 'rejected'

    Args:
        p: Raw payment dict from the ML Payments API.

    Returns:
        True if the payment qualifies as a valid sale.
    """
    order = p.get("order") or {}
    if not order.get("id"):
        return False
    if (p.get("description") or "") == "marketplace_shipment":
        return False
    if (p.get("collector") or {}).get("id") is not None:
        return False
    if p["status"] in ("cancelled", "rejected"):
        return False
    return True


def _is_liberacao(line: dict) -> bool:
    """Return True if the extrato line is a 'Liberacao de dinheiro' release."""
    return "liberacao" in _normalize(line["transaction_type"])


def _compute_daily_divergences(
    extrato_lines: list[dict],
    payments_by_id: dict[int, dict],
) -> dict[str, Decimal]:
    """Compute per-day divergence between computed nets and extrato liberacao amounts.

    For each calendar day that appears in the extrato:
    - Sums computed nets for all liberacao lines whose reference_id resolves to
      a valid sale payment.
    - Sums actual extrato amounts for all liberacao lines on that day.
    - Returns the signed difference (computed - extrato) keyed by ISO date.

    Liberacao lines whose reference_id does NOT resolve to a known payment are
    counted with computed net = extrato amount (i.e., zero divergence), so they
    never mask real errors.

    Args:
        extrato_lines: All extrato lines for the period.
        payments_by_id: Mapping of payment_id (int) -> payment dict.

    Returns:
        Dict mapping ISO date string to signed divergence (Decimal).
    """
    computed_by_day: dict[str, Decimal] = defaultdict(Decimal)
    extrato_by_day: dict[str, Decimal] = defaultdict(Decimal)

    for line in extrato_lines:
        if not _is_liberacao(line):
            continue

        day_iso = _extrato_date_to_iso(line["date"])
        extrato_amount = _d(line["amount"])
        extrato_by_day[day_iso] += extrato_amount

        # Attempt to resolve the reference_id to a payment
        ref_id = line.get("reference_id", "")
        try:
            payment_id = int(ref_id)
        except (ValueError, TypeError):
            # Cannot parse as int — treat computed = extrato (no divergence)
            computed_by_day[day_iso] += extrato_amount
            continue

        payment = payments_by_id.get(payment_id)
        if payment is None or not _is_valid_sale(payment):
            # Payment unknown or not a valid sale — treat computed = extrato
            computed_by_day[day_iso] += extrato_amount
            continue

        _, _, calculated_net = extract_charges(payment)
        computed_by_day[day_iso] += _d(calculated_net)

    # Build divergence dict for every day that appears in the extrato
    all_days = set(computed_by_day) | set(extrato_by_day)
    return {
        day: computed_by_day[day] - extrato_by_day[day]
        for day in sorted(all_days)
    }


# ---------------------------------------------------------------------------
# Test class
# ---------------------------------------------------------------------------


class TestDailyReconciliation:
    """T025-T027: Daily cash reconciliation across January and February 2026."""

    # ------------------------------------------------------------------
    # T025 — January 2026
    # ------------------------------------------------------------------

    def test_daily_totals_match_jan(
        self,
        extrato_jan: list[dict],
        payments_by_id_jan: dict[int, dict],
    ) -> None:
        """T025: Each day in January must have liberacao divergence < R$0.05.

        For every calendar day that has at least one 'Liberacao de dinheiro'
        line in the January extrato, the absolute difference between the sum
        of processor-computed nets and the sum of actual extrato amounts must
        not exceed the daily tolerance.
        """
        divergences = _compute_daily_divergences(extrato_jan, payments_by_id_jan)

        failures: list[str] = []
        for day, divergence in divergences.items():
            abs_div = abs(divergence)
            if abs_div >= DAILY_TOLERANCE:
                failures.append(
                    f"  {day}: computed-extrato divergence = R${divergence:+.2f} "
                    f"(tolerance R${DAILY_TOLERANCE})"
                )

        assert not failures, (
            f"T025 — {len(failures)} day(s) in January exceeded tolerance:\n"
            + "\n".join(failures)
        )

    # ------------------------------------------------------------------
    # T026 — February 2026
    # ------------------------------------------------------------------

    def test_daily_totals_match_fev(
        self,
        extrato_feb: list[dict],
        payments_by_id_feb: dict[int, dict],
    ) -> None:
        """T026: Each day in February must have liberacao divergence < R$0.05.

        Same logic as T025 applied to the February extrato and payments.
        """
        divergences = _compute_daily_divergences(extrato_feb, payments_by_id_feb)

        failures: list[str] = []
        for day, divergence in divergences.items():
            abs_div = abs(divergence)
            if abs_div >= DAILY_TOLERANCE:
                failures.append(
                    f"  {day}: computed-extrato divergence = R${divergence:+.2f} "
                    f"(tolerance R${DAILY_TOLERANCE})"
                )

        assert not failures, (
            f"T026 — {len(failures)} day(s) in February exceeded tolerance:\n"
            + "\n".join(failures)
        )

    # ------------------------------------------------------------------
    # T027 — Cumulative across both months
    # ------------------------------------------------------------------

    def test_cumulative_divergence_under_threshold(
        self,
        extrato_jan: list[dict],
        payments_by_id_jan: dict[int, dict],
        extrato_feb: list[dict],
        payments_by_id_feb: dict[int, dict],
    ) -> None:
        """T027: Sum of absolute daily divergences across Jan+Feb must be < R$5.00.

        Even if individual days pass the R$0.05 per-day tolerance, systematic
        bias would accumulate here. A cumulative divergence above R$5.00 over
        two months signals a structural error in fee extraction.
        """
        jan_divergences = _compute_daily_divergences(extrato_jan, payments_by_id_jan)
        feb_divergences = _compute_daily_divergences(extrato_feb, payments_by_id_feb)

        total_abs = sum(abs(d) for d in jan_divergences.values()) + sum(
            abs(d) for d in feb_divergences.values()
        )

        assert total_abs < CUMULATIVE_TOLERANCE, (
            f"T027 — Cumulative divergence R${total_abs:.2f} exceeds "
            f"threshold R${CUMULATIVE_TOLERANCE} across January+February 2026.\n"
            f"January per-day: { {k: float(v) for k, v in jan_divergences.items()} }\n"
            f"February per-day: { {k: float(v) for k, v in feb_divergences.items()} }"
        )
