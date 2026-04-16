"""ERR-0011 — subscription FX/IOF drift must not block match.

Foreign-currency subscriptions (Supabase, Claude.ai, Notion) charge in
USD/EUR. Extrato shows BRL post-FX + post-IOF (~3.5% above sys amount).
The system stores transaction_amount in BRL pre-IOF.

Contract: when both extrato and system movements have category='subscription'
and the diff is within 5% of either amount, mark as match (not amount_diff).
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from app.services.reconciliation import CashMovement, match_movements


pytestmark = pytest.mark.architecture


def _mov(source: str, ref_id: str, amount: float, category: str, date: str) -> CashMovement:
    return CashMovement(
        date=date,
        ref_id=ref_id,
        amount=Decimal(str(amount)),
        category=category,
        source=source,
    )


class TestSubscriptionIofTolerance:
    def test_supabase_iof_drift_3_5_pct_matches(self):
        """Real case: ext -169.03 vs sys -163.31, diff 3.5% (IOF)."""
        ext = [_mov("extrato", "140496724089", -169.03, "subscription", "2026-01-08")]
        sys = [_mov("mp_expenses", "140496724089", -163.31, "subscription", "2026-01-10")]

        results = match_movements(ext, sys, Decimal("0.10"))

        assert len(results) == 1
        assert results[0].status == "match", (
            f"Subscription within 5% drift must be match, got {results[0].status} "
            f"(diff={results[0].diff})"
        )

    def test_claude_iof_drift_matches(self):
        """Real case: ext -569.25 vs sys -550.00, diff 3.5% (IOF)."""
        ext = [_mov("extrato", "141215405790", -569.25, "subscription", "2026-01-08")]
        sys = [_mov("mp_expenses", "141215405790", -550.00, "subscription", "2026-01-10")]

        results = match_movements(ext, sys, Decimal("0.10"))

        assert len(results) == 1
        assert results[0].status == "match"

    def test_notion_iof_drift_matches(self):
        """Real case: ext -131.94 vs sys -127.48, diff 3.5% (IOF)."""
        ext = [_mov("extrato", "143199074090", -131.94, "subscription", "2026-01-23")]
        sys = [_mov("mp_expenses", "143199074090", -127.48, "subscription", "2026-01-25")]

        results = match_movements(ext, sys, Decimal("0.10"))

        assert len(results) == 1
        assert results[0].status == "match"

    def test_subscription_drift_above_5pct_remains_amount_diff(self):
        """Drift of 10% should still be amount_diff (signal of real bug)."""
        ext = [_mov("extrato", "999999", -100.00, "subscription", "2026-01-01")]
        sys = [_mov("mp_expenses", "999999", -90.00, "subscription", "2026-01-01")]

        results = match_movements(ext, sys, Decimal("0.10"))

        assert results[0].status == "amount_diff"

    def test_non_subscription_categories_keep_strict_tolerance(self):
        """Tolerance widening must not apply to other categories."""
        ext = [_mov("extrato", "888888", -100.00, "pagamento_conta", "2026-01-01")]
        sys = [_mov("mp_expenses", "888888", -103.00, "pagamento_conta", "2026-01-01")]

        results = match_movements(ext, sys, Decimal("0.10"))

        assert results[0].status == "amount_diff", (
            "Non-subscription categories must use strict per_line tolerance, "
            f"got {results[0].status}"
        )
