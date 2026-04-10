"""
Phase 6 — Full Extrato Coverage Tests (T020-T022)

Verifies that the extrato classification rules cover 100% of the January 2026
account_statement lines for 141air, and audits the cash impact of unconditional
skips to detect silent leakage.

Run: python3 -m pytest testes/integration/test_cash_coverage.py -v -s
"""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Optional

import pytest

from app.services.extrato_ingester import (
    EXTRATO_CLASSIFICATION_RULES,
    _CHECK_PAYMENTS,
    _classify_extrato_line,
    _parse_account_statement,
)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

EXTRATO_PATH = (
    Path(__file__).parent.parent / "data" / "extratos" / "extrato janeiro 141Air.csv"
)

# ---------------------------------------------------------------------------
# Classification helpers
# ---------------------------------------------------------------------------


def classify_line(transaction_type: str) -> tuple[str | None, str | None, str | None]:
    """Return (expense_type, direction, ca_category) for a transaction type.

    Mirrors the matching logic inside extrato_ingester._classify_extrato_line
    without requiring a live DB connection (no _CHECK_PAYMENTS resolution).
    Returns ("unclassified", None, None) when no rule matches.
    """
    import unicodedata

    def _norm(s: str) -> str:
        return unicodedata.normalize("NFD", s).encode("ascii", "ignore").decode().lower()

    lower = _norm(transaction_type)
    for pattern, expense_type, direction, cat in EXTRATO_CLASSIFICATION_RULES:
        if _norm(pattern) in lower:
            return expense_type, direction, cat
    return "unclassified", None, None


def is_unconditional_skip(expense_type: str | None, direction: str | None) -> bool:
    """True when the rule silently discards the line with no further processing."""
    return expense_type is None and direction is None


def is_conditional_skip(expense_type: str | None) -> bool:
    """True when the rule delegates to payment_events for a final decision."""
    return expense_type == _CHECK_PAYMENTS


# ---------------------------------------------------------------------------
# Session-scoped fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def extrato_transactions() -> list[dict]:
    """Parse January 2026 extrato CSV and return all transaction rows."""
    csv_text = EXTRATO_PATH.read_text(encoding="utf-8-sig")
    _summary, transactions = _parse_account_statement(csv_text)
    return transactions


@pytest.fixture(scope="session")
def extrato_summary() -> dict:
    """Return the balance summary section of the January 2026 extrato."""
    csv_text = EXTRATO_PATH.read_text(encoding="utf-8-sig")
    summary, _transactions = _parse_account_statement(csv_text)
    return summary


@pytest.fixture(scope="session")
def classified_lines(extrato_transactions) -> list[dict]:
    """Attach classification metadata to each transaction row."""
    result = []
    for tx in extrato_transactions:
        tx_type = tx.get("transaction_type", "")
        expense_type, direction, ca_category = classify_line(tx_type)
        result.append(
            {
                **tx,
                "_expense_type": expense_type,
                "_direction": direction,
                "_ca_category": ca_category,
                "_is_unconditional_skip": is_unconditional_skip(expense_type, direction),
                "_is_conditional_skip": is_conditional_skip(expense_type),
                "_is_captured": (
                    expense_type is not None
                    and expense_type != _CHECK_PAYMENTS
                    and direction is not None
                ),
                "_is_unclassified": expense_type == "unclassified",
            }
        )
    return result


# ---------------------------------------------------------------------------
# T020 — All lines must match at least one classification rule
# ---------------------------------------------------------------------------


class TestExtratoCoverage:
    def test_all_lines_classified(self, classified_lines):
        """T020: Every extrato line must match a classification rule.

        Categories:
          - conditional_skip: rule is _CHECK_PAYMENTS (needs DB lookup)
          - captured:         has expense_type and direction → goes to mp_expenses
          - unconditional_skip: expense_type=None and direction=None → silently dropped
          - unclassified:     no rule matched (BUG — new tx type needs a rule)

        Assertion: unclassified == 0.
        """
        total = len(classified_lines)
        conditional_skips = [l for l in classified_lines if l["_is_conditional_skip"]]
        captured = [l for l in classified_lines if l["_is_captured"]]
        unconditional_skips = [l for l in classified_lines if l["_is_unconditional_skip"]]
        unclassified = [l for l in classified_lines if l["_is_unclassified"]]

        # Print a human-readable summary for -s output
        print(f"\n{'=' * 60}")
        print(f"T020: Extrato classification summary (January 2026 — 141air)")
        print(f"{'=' * 60}")
        print(f"  Total lines          : {total}")
        print(f"  Conditional skips    : {len(conditional_skips)}")
        print(f"  Captured             : {len(captured)}")
        print(f"  Unconditional skips  : {len(unconditional_skips)}")
        print(f"  Unclassified (BUG!)  : {len(unclassified)}")

        if unclassified:
            unique_types = sorted({l["transaction_type"] for l in unclassified})
            print(f"\n  Unclassified tx types ({len(unique_types)} unique):")
            for t in unique_types:
                count = sum(1 for l in unclassified if l["transaction_type"] == t)
                print(f"    [{count:3d}x] {t!r}")

        assert len(unclassified) == 0, (
            f"{len(unclassified)} extrato line(s) matched no rule. "
            f"Add rules to EXTRATO_CLASSIFICATION_RULES for: "
            f"{sorted({l['transaction_type'] for l in unclassified})}"
        )

    # ---------------------------------------------------------------------------
    # T021 — Unconditional skips net cash impact audit
    # ---------------------------------------------------------------------------

    def test_unconditional_skips_have_zero_cash_impact(self, classified_lines):
        """T021: Report the net cash impact of each unconditional-skip pattern.

        Unconditional skips are dropped with no CA entry.  If any skip pattern
        has a nonzero net amount it means real cash flow is being silently
        ignored.

        The test is INFORMATIONAL: it prints per-pattern sums and flags any
        pattern with |net| > R$0.01.  Patterns that genuinely move money (e.g.
        "transferencia pix") must either be promoted to captured rules or
        documented as intentionally excluded.
        """
        from collections import defaultdict

        pattern_totals: dict[str, Decimal] = defaultdict(Decimal)
        pattern_counts: dict[str, int] = defaultdict(int)

        for line in classified_lines:
            if not line["_is_unconditional_skip"]:
                continue
            tx_type = line.get("transaction_type", "")
            amount = Decimal(str(line.get("amount", 0)))

            # Identify which pattern matched this line
            import unicodedata

            def _norm(s: str) -> str:
                return (
                    unicodedata.normalize("NFD", s)
                    .encode("ascii", "ignore")
                    .decode()
                    .lower()
                )

            matched_pattern = "unknown"
            lower_type = _norm(tx_type)
            for pattern, expense_type, direction, _cat in EXTRATO_CLASSIFICATION_RULES:
                if _norm(pattern) in lower_type and expense_type is None and direction is None:
                    matched_pattern = pattern
                    break

            pattern_totals[matched_pattern] += amount
            pattern_counts[matched_pattern] += 1

        print(f"\n{'=' * 60}")
        print("T021: Unconditional-skip pattern cash impact")
        print(f"{'=' * 60}")

        non_zero_patterns: list[str] = []
        for pattern in sorted(pattern_totals):
            net = pattern_totals[pattern]
            count = pattern_counts[pattern]
            flag = "  <-- NON-ZERO" if abs(net) > Decimal("0.01") else ""
            print(f"  {pattern!r:40s} count={count:4d}  net={net:>12.2f}{flag}")
            if abs(net) > Decimal("0.01"):
                non_zero_patterns.append(pattern)

        if non_zero_patterns:
            print(
                f"\n  WARNING: {len(non_zero_patterns)} skip pattern(s) have nonzero "
                f"net cash impact and may be silently dropping real transactions."
            )

        # Fail to draw attention — these patterns need explicit documentation or
        # promotion to captured rules.
        assert not non_zero_patterns, (
            f"Unconditional-skip patterns with nonzero net impact: {non_zero_patterns}. "
            f"Either add a captured rule for these tx types or document why the cash "
            f"impact is acceptable to ignore."
        )

    # ---------------------------------------------------------------------------
    # T022 — Full coverage sanity check: all amounts account for
    # ---------------------------------------------------------------------------

    def test_no_cash_impact_lines_missing(self, classified_lines, extrato_summary):
        """T022: Captured + conditional_skips + unconditional_skips ≈ total.

        Sums amounts in each category and verifies they reconstruct the total
        extrato cash flow (difference < R$0.05).  This detects any lines that
        were accidentally dropped by the classification loop.
        """
        TOLERANCE = Decimal("0.05")

        total_amount = Decimal("0")
        captured_amount = Decimal("0")
        conditional_amount = Decimal("0")
        unconditional_amount = Decimal("0")
        unclassified_amount = Decimal("0")

        for line in classified_lines:
            amount = Decimal(str(line.get("amount", 0)))
            total_amount += amount

            if line["_is_unclassified"]:
                unclassified_amount += amount
            elif line["_is_conditional_skip"]:
                conditional_amount += amount
            elif line["_is_unconditional_skip"]:
                unconditional_amount += amount
            elif line["_is_captured"]:
                captured_amount += amount

        reconstructed = captured_amount + conditional_amount + unconditional_amount
        gap = abs(total_amount - reconstructed)

        print(f"\n{'=' * 60}")
        print("T022: Cash coverage sanity check")
        print(f"{'=' * 60}")
        print(f"  Total extrato amount      : {total_amount:>14.2f}")
        print(f"  Captured                  : {captured_amount:>14.2f}")
        print(f"  Conditional skips         : {conditional_amount:>14.2f}")
        print(f"  Unconditional skips       : {unconditional_amount:>14.2f}")
        print(f"  Reconstructed total       : {reconstructed:>14.2f}")
        print(f"  Gap (should be < 0.05)    : {gap:>14.2f}")
        if unclassified_amount:
            print(f"  Unclassified (not in sum) : {unclassified_amount:>14.2f}  <-- BUG")

        assert gap <= TOLERANCE, (
            f"Cash coverage gap R${gap:.2f} exceeds tolerance R${TOLERANCE}. "
            f"Some extrato lines are not being categorised. "
            f"Total={total_amount:.2f}, Reconstructed={reconstructed:.2f}"
        )
