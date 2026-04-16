"""ERR-0009 — load_mp_expenses must include rows on period_start.

`mp_expenses.date_approved` is a text column in `YYYY-MM-DD` format. The
previous loader filtered with a timestamp suffix (`'2026-01-01T00:00:00'`),
which under lexicographic text comparison silently excluded every row whose
date equals period_start exactly (since `'2026-01-01' < '2026-01-01T...'`).

Contract: the loader must pass period strings in the SAME format as the
column (plain date).
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest


pytestmark = pytest.mark.classifier


class _CapturingDB:
    """Minimal fake that records the bounds passed to gte/lte."""

    def __init__(self, rows: list[dict] | None = None):
        self.rows = rows or []
        self.captured: dict[str, str] = {}

    def table(self, name):
        assert name == "mp_expenses"
        return self

    def select(self, _cols):
        return self

    def eq(self, _field, _value):
        return self

    def gte(self, field, value):
        if field == "date_approved":
            self.captured["gte"] = value
        return self

    def lte(self, field, value):
        if field == "date_approved":
            self.captured["lte"] = value
        return self

    def range(self, _lo, _hi):
        return self

    def execute(self):
        return MagicMock(data=self.rows)


class TestLoadMpExpensesBoundary:
    def test_filter_uses_plain_date_format(self):
        """Filters must be plain YYYY-MM-DD, not ISO timestamp strings."""
        from app.services.reconciliation import load_mp_expenses

        db = _CapturingDB()
        load_mp_expenses(db, "141air", "2026-01-01", "2026-01-31")

        assert db.captured["gte"] == "2026-01-01", (
            f"gte filter must be plain date; got {db.captured['gte']!r}. "
            "With ISO timestamp, rows where date_approved='2026-01-01' are "
            "excluded lexicographically."
        )
        assert db.captured["lte"] == "2026-01-31"

    def test_period_start_rows_are_returned(self):
        """Integration-ish: rows on the first day of the period flow through."""
        from app.services.reconciliation import load_mp_expenses

        rows = [
            {"payment_id": "X1", "date_approved": "2026-01-01", "amount": 10},
            {"payment_id": "X2", "date_approved": "2026-01-15", "amount": 20},
        ]
        db = _CapturingDB(rows)
        loaded = load_mp_expenses(db, "141air", "2026-01-01", "2026-01-31")
        # The fake DB returns all rows unconditionally — this asserts we at
        # least issued a request and returned them.
        assert {r["payment_id"] for r in loaded} == {"X1", "X2"}
