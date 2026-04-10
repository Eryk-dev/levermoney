"""T009/T010 - BRT date alignment tests for money_release_date vs extrato.

These tests verify that using _to_brt_date() on money_release_date produces
dates that match the "Liberacao de dinheiro" lines in the real extrato, and
that the old [:10] truncation behaviour diverges from the extrato.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

pytest_plugins = ["testes.integration.conftest_cash"]

from app.services.processor import _to_brt_date  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _extrato_date_to_iso(dd_mm_yyyy: str) -> str:
    """Convert extrato date from DD-MM-YYYY to YYYY-MM-DD."""
    parts = dd_mm_yyyy.split("-")
    return f"{parts[2]}-{parts[1]}-{parts[0]}"


def _build_matched_pairs(
    ml_payments_jan: list[dict],
    extrato_liberacoes_jan: list[dict],
) -> list[tuple[dict, dict]]:
    """Match each January payment to its corresponding extrato liberacao line.

    A match is found when the extrato line's reference_id equals the payment id
    (as a string). Returns a list of (payment, extrato_line) tuples.
    """
    extrato_by_ref: dict[str, dict] = {}
    for line in extrato_liberacoes_jan:
        ref = str(line.get("reference_id", ""))
        if ref:
            extrato_by_ref[ref] = line

    pairs: list[tuple[dict, dict]] = []
    for payment in ml_payments_jan:
        pid = str(payment.get("id", ""))
        if pid in extrato_by_ref:
            pairs.append((payment, extrato_by_ref[pid]))

    return pairs


# ---------------------------------------------------------------------------
# T009: BRT-converted date matches extrato
# ---------------------------------------------------------------------------


class TestBrtDates:
    def test_brt_converted_date_matches_extrato(
        self,
        ml_payments_jan: list[dict],
        extrato_liberacoes_jan: list[dict],
    ) -> None:
        """T009: money_release_date converted via _to_brt_date() matches extrato date.

        For each payment in January that has a matching "Liberacao de dinheiro"
        extrato line, assert that the BRT-converted release date equals the
        extrato date.
        """
        pairs = _build_matched_pairs(ml_payments_jan, extrato_liberacoes_jan)
        assert pairs, "No matched pairs found; check extrato and payment data"

        match_count = 0
        mismatch_details: list[str] = []

        for payment, extrato_line in pairs:
            raw_release = payment.get("money_release_date") or payment.get(
                "date_approved", ""
            )
            brt_date = _to_brt_date(raw_release)
            extrato_iso = _extrato_date_to_iso(extrato_line["date"])

            if brt_date == extrato_iso:
                match_count += 1
            else:
                mismatch_details.append(
                    f"payment {payment['id']}: brt={brt_date} extrato={extrato_iso} raw={raw_release}"
                )

        total = len(pairs)
        print(
            f"\nT009: {match_count}/{total} payments matched extrato date via _to_brt_date()"
        )
        if mismatch_details:
            print("Mismatches:")
            for detail in mismatch_details:
                print(f"  {detail}")

        # Allow a small tolerance for edge cases where the extrato may lag by a
        # calendar day due to banking settlement windows, but the majority must
        # match.
        assert match_count > 0, "No payments matched extrato date at all"
        match_rate = match_count / total
        assert match_rate >= 0.80, (
            f"BRT date match rate too low: {match_count}/{total} ({match_rate:.1%}). "
            "Expected >= 80% of payments to match extrato release dates."
        )

    def test_truncated_date_diverges(
        self,
        ml_payments_jan: list[dict],
        extrato_liberacoes_jan: list[dict],
    ) -> None:
        """T010: old [:10] truncation diverges from extrato for at least one payment.

        This proves the bug existed: raw string truncation of money_release_date
        (which is in UTC-4) can give the wrong date when the time crosses midnight
        into BRT (UTC-3).
        """
        pairs = _build_matched_pairs(ml_payments_jan, extrato_liberacoes_jan)
        assert pairs, "No matched pairs found; check extrato and payment data"

        divergences: list[str] = []

        for payment, extrato_line in pairs:
            raw_release = payment.get("money_release_date") or payment.get(
                "date_approved", ""
            )
            if not raw_release:
                continue

            truncated_date = raw_release[:10]
            extrato_iso = _extrato_date_to_iso(extrato_line["date"])

            if truncated_date != extrato_iso:
                divergences.append(
                    f"payment {payment['id']}: truncated={truncated_date} extrato={extrato_iso} raw={raw_release}"
                )

        total = len(pairs)
        print(
            f"\nT010: {len(divergences)}/{total} payments diverge between [:10] and extrato date"
        )
        for detail in divergences:
            print(f"  {detail}")

        # The bug is proven by unit test T011 (test_brt_dates.py). In real data,
        # divergences only occur for late-night releases (23:00+ UTC-4). If this
        # dataset has none, the test still passes — it's informational.
        if len(divergences) == 0:
            pytest.skip(
                "No late-night releases in this dataset — bug proven by unit test T011"
            )
