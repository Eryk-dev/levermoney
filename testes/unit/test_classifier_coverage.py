"""T-020 (classifier coverage) — every TRANSACTION_TYPE seen in production
extratos must have an explicit classification rule.

Loads every extrato CSV in `testes/data/extratos/`, extracts every distinct
TRANSACTION_TYPE value, and asserts that `_classify_extrato_line` returns
something other than the fallback `("other", "expense", None)`. Falling
back to `"other"` means the rule table missed a real-world transaction
type and the line silently goes to pending_review.

If this test fails, add an explicit rule to
`EXTRATO_CLASSIFICATION_RULES` in `app/services/extrato_ingester.py`.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.services.extrato_ingester import (
    _classify_extrato_line,
    _parse_account_statement,
)


pytestmark = pytest.mark.classifier


EXTRATOS_DIR = Path(__file__).resolve().parent.parent / "data" / "extratos"


def _load_all_transaction_types() -> set[str]:
    types: set[str] = set()
    for csv_path in EXTRATOS_DIR.glob("*.csv"):
        text = csv_path.read_text(encoding="utf-8-sig")
        _summary, transactions = _parse_account_statement(text)
        for tx in transactions:
            tx_type = tx.get("transaction_type")
            if tx_type:
                types.add(tx_type)
    return types


def test_extratos_directory_has_csvs() -> None:
    csvs = list(EXTRATOS_DIR.glob("*.csv"))
    assert len(csvs) > 0, f"no extrato CSVs found under {EXTRATOS_DIR}"


def test_no_unknown_extrato_types() -> None:
    """Every TRANSACTION_TYPE in real extratos must classify explicitly.

    Falling back to ('other', 'expense', None) means we missed a rule.
    """
    types = _load_all_transaction_types()
    assert types, "expected at least one transaction across all CSVs"

    unknown: list[str] = []
    for tx_type in sorted(types):
        expense_type, direction, _cat = _classify_extrato_line(tx_type)
        if expense_type == "other" and direction == "expense":
            unknown.append(tx_type)

    assert not unknown, (
        f"{len(unknown)} TRANSACTION_TYPE(s) without explicit classification:\n"
        + "\n".join(f"  • {t!r}" for t in unknown[:20])
        + (f"\n  ... and {len(unknown) - 20} more" if len(unknown) > 20 else "")
        + "\n\nAdd rules to EXTRATO_CLASSIFICATION_RULES in extrato_ingester.py."
    )
