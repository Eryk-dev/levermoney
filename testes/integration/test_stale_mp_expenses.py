"""T-011 (ERR-0002) — stale expense_captured events detection.

I-8 invariant (spec.md §3): an expense_captured event with expense_type in
{liberacao_nao_sync, qr_pix_nao_sync, pix_nao_sync, dinheiro_recebido} is
*stale* when the same ml_payment_id later receives a sale_approved event.

The reconciliation pipeline must:
  1. Detect such stale events offline (pure function).
  2. Mark them (or delete them) so the matcher doesn't double-count.

The unit-level test below exercises the pure detection function with a
synthetic event ledger snapshot — no DB required.
"""
from __future__ import annotations

import pytest

from app.services.extrato_ingester import (
    STALE_EXPENSE_TYPES,
    find_stale_expense_events,
)


pytestmark = pytest.mark.stale_data


def _expense(payment_id: int, expense_type: str, ref_id: str | None = None) -> dict:
    return {
        "id": f"evt-{payment_id}-{expense_type}",
        "ml_payment_id": payment_id,
        "reference_id": ref_id or f"{payment_id}",
        "event_type": "expense_captured",
        "metadata": {"expense_type": expense_type},
    }


def _sale(payment_id: int) -> dict:
    return {
        "id": f"evt-{payment_id}-sale",
        "ml_payment_id": payment_id,
        "reference_id": str(payment_id),
        "event_type": "sale_approved",
        "metadata": {},
    }


# ─── Detection function tests ────────────────────────────────────────────


class TestFindStaleExpenseEvents:
    def test_empty_returns_empty(self):
        assert find_stale_expense_events([]) == []

    def test_expense_without_sale_is_not_stale(self):
        events = [_expense(100, "liberacao_nao_sync")]
        assert find_stale_expense_events(events) == []

    def test_sale_without_matching_stale_expense_is_not_stale(self):
        events = [_sale(100), _expense(200, "liberacao_nao_sync")]
        # Different payment_ids — only #200 has expense, only #100 has sale.
        assert find_stale_expense_events(events) == []

    def test_liberacao_nao_sync_with_sale_is_stale(self):
        events = [_expense(100, "liberacao_nao_sync"), _sale(100)]
        stale = find_stale_expense_events(events)
        assert len(stale) == 1
        assert stale[0]["id"] == "evt-100-liberacao_nao_sync"

    def test_qr_pix_nao_sync_with_sale_is_stale(self):
        events = [_expense(101, "qr_pix_nao_sync"), _sale(101)]
        assert len(find_stale_expense_events(events)) == 1

    def test_pix_nao_sync_with_sale_is_stale(self):
        events = [_expense(102, "pix_nao_sync"), _sale(102)]
        assert len(find_stale_expense_events(events)) == 1

    def test_dinheiro_recebido_with_sale_is_stale(self):
        events = [_expense(103, "dinheiro_recebido"), _sale(103)]
        assert len(find_stale_expense_events(events)) == 1

    def test_non_stale_expense_type_with_sale_is_not_stale(self):
        """e.g. difal, faturas_ml — these are real expenses with their own ref_id."""
        for expense_type in ("difal", "faturas_ml", "subscription", "bill_payment"):
            events = [_expense(200 + i, expense_type) for i, expense_type in enumerate(["difal", "faturas_ml"])]
            events.append(_sale(200))
            assert find_stale_expense_events(events) == [], (
                f"expense_type={expense_type!r} must NOT be flagged as stale"
            )

    def test_mixed_stale_and_clean_expenses(self):
        events = [
            # Stale: stale type + matching sale
            _expense(100, "liberacao_nao_sync"),
            _sale(100),
            # Stale: stale type + matching sale (different payment)
            _expense(101, "qr_pix_nao_sync"),
            _sale(101),
            # Clean: expense without sale
            _expense(102, "liberacao_nao_sync"),
            # Clean: non-stale type even though sale exists
            _expense(103, "difal"),
            _sale(103),
        ]
        stale_ids = {e["id"] for e in find_stale_expense_events(events)}
        assert stale_ids == {
            "evt-100-liberacao_nao_sync",
            "evt-101-qr_pix_nao_sync",
        }

    def test_multiple_stale_for_same_payment(self):
        """Edge case: same payment_id has both liberacao_nao_sync AND qr_pix_nao_sync.
        Both should be flagged."""
        events = [
            _expense(105, "liberacao_nao_sync", ref_id="105:ln"),
            _expense(105, "qr_pix_nao_sync", ref_id="105:qn"),
            _sale(105),
        ]
        stale = find_stale_expense_events(events)
        assert len(stale) == 2

    def test_skips_non_expense_captured_events(self):
        """expense_classified, expense_reviewed should not be flagged."""
        events = [
            {
                "id": "evt-classified",
                "ml_payment_id": 110,
                "event_type": "expense_classified",
                "metadata": {"expense_type": "liberacao_nao_sync"},
            },
            _sale(110),
        ]
        assert find_stale_expense_events(events) == []


class TestStaleExpenseTypesContract:
    def test_contract_matches_spec(self):
        """STALE_EXPENSE_TYPES must match the contract.yml stale_mp_expense.affected_types."""
        expected = {
            "liberacao_nao_sync",
            "qr_pix_nao_sync",
            "pix_nao_sync",
            "dinheiro_recebido",
        }
        assert set(STALE_EXPENSE_TYPES) == expected
