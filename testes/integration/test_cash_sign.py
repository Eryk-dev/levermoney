"""Phase 5 — Sign Correctness for Non-Order Payments (T014-T017).

Verifies that the direction declared in EXTRATO_CLASSIFICATION_RULES matches
the actual sign of each extrato line:
  - amount > 0  (credit / money IN)  →  direction == "income"
  - amount < 0  (debit  / money OUT) →  direction == "expense"

Order-related lines ("Liberacao de dinheiro", "Pagamento com") are skipped
because they are handled by the Payments API, not the extrato ingester.
"""
from __future__ import annotations

import unicodedata
from typing import Optional

import pytest

from app.services.extrato_ingester import EXTRATO_CLASSIFICATION_RULES, _CHECK_PAYMENTS

pytest_plugins = ["testes.integration.conftest_cash"]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_ORDER_PATTERNS = (
    "liberacao de dinheiro",
    "liberação de dinheiro",
    "pagamento com",
)


def _normalise(text: str) -> str:
    """Lowercase + strip accents (mirrors extrato_ingester normalisation)."""
    nfkd = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def classify_line(transaction_type: str) -> tuple[Optional[str], Optional[str]]:
    """Return (expense_type, direction) for a transaction_type string.

    Mirrors the first-match logic in extrato_ingester.ingest_extrato_gaps().
    Returns ("unclassified", None) when no rule matches.
    """
    lower = _normalise(transaction_type)
    for pattern, expense_type, direction, _cat in EXTRATO_CLASSIFICATION_RULES:
        if pattern in lower:
            return expense_type, direction
    return "unclassified", None


def _is_order_line(transaction_type: str) -> bool:
    """Return True for lines processed by the Payments API (not non-order ingester)."""
    lower = _normalise(transaction_type)
    return any(pat in lower for pat in _ORDER_PATTERNS)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestSignCorrectness:
    """T014-T017: Verify that extrato sign aligns with classification direction."""

    # ------------------------------------------------------------------
    # T014 — Credits (amount > 0) must map to direction "income"
    # ------------------------------------------------------------------

    def test_extrato_credits_are_positive(self, extrato_jan: list[dict]) -> None:
        """T014: Every classified credit line in Jan extrato has direction 'income'."""
        correct = 0
        incorrect: list[dict] = []

        for line in extrato_jan:
            amount: float = line["amount"]
            if amount <= 0:
                continue
            if _is_order_line(line["transaction_type"]):
                continue

            expense_type, direction = classify_line(line["transaction_type"])

            # Unconditional-skip rules (expense_type=None) are internal and
            # do not generate financial records — nothing to assert.
            if expense_type is None:
                continue
            # Unclassified lines have no declared direction yet — skip.
            if expense_type == "unclassified":
                continue

            if direction == "income":
                correct += 1
            else:
                incorrect.append(
                    {
                        "transaction_type": line["transaction_type"],
                        "amount": amount,
                        "expense_type": expense_type,
                        "direction": direction,
                        "reference_id": line.get("reference_id"),
                    }
                )

        assert incorrect == [], (
            f"T014 FAIL — {len(incorrect)} credit line(s) have direction != 'income'.\n"
            + "\n".join(str(r) for r in incorrect)
        )
        assert correct > 0, (
            "T014: No classified credit lines found in Jan extrato — "
            "check fixture or classification rules."
        )

    # ------------------------------------------------------------------
    # T015 — Debits (amount < 0) must map to direction "expense"
    # ------------------------------------------------------------------

    def test_extrato_debits_are_negative(self, extrato_jan: list[dict]) -> None:
        """T015: Every classified debit line in Jan extrato has direction 'expense'."""
        correct = 0
        incorrect: list[dict] = []

        for line in extrato_jan:
            amount: float = line["amount"]
            if amount >= 0:
                continue
            if _is_order_line(line["transaction_type"]):
                continue

            expense_type, direction = classify_line(line["transaction_type"])

            if expense_type is None:
                continue
            if expense_type == "unclassified":
                continue

            if direction == "expense":
                correct += 1
            else:
                incorrect.append(
                    {
                        "transaction_type": line["transaction_type"],
                        "amount": amount,
                        "expense_type": expense_type,
                        "direction": direction,
                        "reference_id": line.get("reference_id"),
                    }
                )

        assert incorrect == [], (
            f"T015 FAIL — {len(incorrect)} debit line(s) have direction != 'expense'.\n"
            + "\n".join(str(r) for r in incorrect)
        )
        assert correct > 0, (
            "T015: No classified debit lines found in Jan extrato — "
            "check fixture or classification rules."
        )

    # ------------------------------------------------------------------
    # T016 — "Entrada de dinheiro" lines are credits and classified income
    # ------------------------------------------------------------------

    def test_deposit_sign_is_positive(self, extrato_jan: list[dict]) -> None:
        """T016: 'Entrada de dinheiro' lines have amount > 0 and direction 'income'."""
        deposit_lines = [
            line
            for line in extrato_jan
            if "entrada de dinheiro" in _normalise(line["transaction_type"])
        ]

        if not deposit_lines:
            pytest.skip("No 'Entrada de dinheiro' lines found in Jan extrato.")

        sign_errors: list[dict] = []
        direction_errors: list[dict] = []

        for line in deposit_lines:
            amount: float = line["amount"]
            _, direction = classify_line(line["transaction_type"])

            if amount <= 0:
                sign_errors.append(
                    {
                        "transaction_type": line["transaction_type"],
                        "amount": amount,
                        "reference_id": line.get("reference_id"),
                    }
                )

            if direction != "income":
                direction_errors.append(
                    {
                        "transaction_type": line["transaction_type"],
                        "amount": amount,
                        "direction": direction,
                        "reference_id": line.get("reference_id"),
                    }
                )

        assert sign_errors == [], (
            f"T016 FAIL — {len(sign_errors)} 'Entrada de dinheiro' line(s) have "
            f"amount <= 0 (expected positive credit).\n"
            + "\n".join(str(r) for r in sign_errors)
        )
        assert direction_errors == [], (
            f"T016 FAIL — {len(direction_errors)} 'Entrada de dinheiro' line(s) are "
            f"classified with direction != 'income'.\n"
            + "\n".join(str(r) for r in direction_errors)
        )

    # ------------------------------------------------------------------
    # T017 — Transfer lines: extrato sign matches classification direction
    # ------------------------------------------------------------------

    def test_transfer_intra_sign_matches_extrato(
        self, extrato_jan: list[dict]
    ) -> None:
        """T017: Transfer lines (excl. 'enviada') have sign consistent with direction.

        'Transferencia Pix enviada' and 'Transferencia enviada' are unconditional
        skips (expense_type=None) and are excluded.  We only check lines where
        the classifier assigns a direction.
        """
        _SKIP_TRANSFER_PATTERNS = (
            "transferencia pix",
            "transferência pix",
            "pix enviado",
            "transferencia enviada",
            "transferência enviada",
            "transferencia de saldo",
            "transferência de saldo",
        )

        transfer_lines = [
            line
            for line in extrato_jan
            if "transfer" in _normalise(line["transaction_type"])
        ]

        if not transfer_lines:
            pytest.skip("No transfer lines found in Jan extrato.")

        mismatches: list[dict] = []

        for line in transfer_lines:
            tx_type = line["transaction_type"]
            lower = _normalise(tx_type)

            # Skip unconditional-skip transfer patterns
            if any(pat in lower for pat in _SKIP_TRANSFER_PATTERNS):
                continue

            expense_type, direction = classify_line(tx_type)

            # Unconditional-skip or unclassified — no direction to verify
            if expense_type is None or expense_type == "unclassified":
                continue

            amount: float = line["amount"]
            expected_direction = "income" if amount > 0 else "expense"

            if direction != expected_direction:
                mismatches.append(
                    {
                        "transaction_type": tx_type,
                        "amount": amount,
                        "classified_direction": direction,
                        "expected_direction": expected_direction,
                        "expense_type": expense_type,
                        "reference_id": line.get("reference_id"),
                    }
                )

        assert mismatches == [], (
            f"T017 FAIL — {len(mismatches)} transfer line(s) have direction mismatch.\n"
            + "\n".join(str(r) for r in mismatches)
        )
