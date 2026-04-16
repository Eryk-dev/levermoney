"""ERR-0006 — load_payment_events must capture full lifecycle of in-period releases.

Bug: when a payment is approved in month A but released in month B, the
period query (month B) only loads the `money_released` event. The historical
`sale_approved`/`fee_charged`/`shipping_charged` from month A are silently
dropped, so `events_to_payment_movements` can't assemble the release group
and the extrato "Liberação" line becomes an orphan.

This test asserts the contract: once ANY event of a pid is loaded for the
period, ALL events of that pid must be retrievable so the release-group
NET can be computed.
"""
from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock

import pytest


pytestmark = pytest.mark.stale_data  # same family as ERR-0002 (data completeness)


# ─── Helpers ──────────────────────────────────────────────────────────────


def _evt(
    pid: int, etype: str, amount: float, event_date: str,
    money_release_date: str | None = None, competencia: str | None = None,
) -> dict:
    return {
        "id": f"{pid}:{etype}",
        "ml_payment_id": pid,
        "seller_slug": "141air",
        "event_type": etype,
        "signed_amount": amount,
        "event_date": event_date,
        "competencia_date": competencia or event_date,
        "metadata": {"money_release_date": money_release_date} if money_release_date else {},
    }


class _MockDB:
    """Mimics the supabase client enough for load_payment_events.

    Stores events keyed by (filter_field, value) pairs. Returns all events
    matching the filter on execute().
    """
    def __init__(self, events: list[dict]):
        self._events = events
        self._filters: list[tuple[str, str, object]] = []
        self._in_filter: tuple[str, list] | None = None

    def table(self, name):
        assert name == "payment_events"
        self._filters = []
        self._in_filter = None
        return self

    def select(self, _cols):
        return self

    def eq(self, field, value):
        self._filters.append(("eq", field, value))
        return self

    def gte(self, field, value):
        self._filters.append(("gte", field, value))
        return self

    def lte(self, field, value):
        self._filters.append(("lte", field, value))
        return self

    def in_(self, field, values):
        self._in_filter = (field, list(values))
        return self

    def range(self, lo, hi):
        self._range = (lo, hi)
        return self

    def execute(self):
        result = []
        for e in self._events:
            ok = True
            for op, field, value in self._filters:
                v = e.get(field)
                if op == "eq" and v != value:
                    ok = False; break
                if op == "gte" and (v is None or v < value):
                    ok = False; break
                if op == "lte" and (v is None or v > value):
                    ok = False; break
            if ok and self._in_filter is not None:
                field, values = self._in_filter
                if e.get(field) not in values:
                    ok = False
            if ok:
                result.append(e)
        lo, hi = getattr(self, "_range", (0, 10_000))
        result = result[lo:hi + 1]
        return MagicMock(data=result)


# ─── Tests ───────────────────────────────────────────────────────────────


class TestLoadPaymentEventsHistory:
    """Events outside the period must be loaded when any pid event is inside."""

    def test_historical_sale_approved_loaded_when_release_in_period(self):
        from app.services.reconciliation import load_payment_events

        dec = "2025-12-19"
        jan = "2026-01-13"
        events = [
            _evt(138580747200, "sale_approved", 52.15, dec, money_release_date=jan),
            _evt(138580747200, "fee_charged", -12.75, dec),
            _evt(138580747200, "money_released", 0.0, jan),
        ]
        db = _MockDB(events)

        loaded = load_payment_events(db, "141air", "2026-01-01", "2026-01-31")

        types = sorted(e["event_type"] for e in loaded if e["ml_payment_id"] == 138580747200)
        assert types == ["fee_charged", "money_released", "sale_approved"], (
            f"Expected full lifecycle; got {types}. "
            "When money_released is in-period, sale_approved + fees must be fetched too."
        )

    def test_release_group_net_reconstructed(self):
        """End-to-end: given the lifecycle, events_to_payment_movements emits
        a release group on the release date with NET = sale + fee + shipping."""
        from app.services.reconciliation import (
            events_to_payment_movements, load_payment_events,
        )

        dec = "2025-12-19"
        jan = "2026-01-13"
        events = [
            _evt(138580747200, "sale_approved", 52.15, dec, money_release_date=jan),
            _evt(138580747200, "fee_charged", -12.75, dec),
            _evt(138580747200, "money_released", 0.0, jan),
        ]
        db = _MockDB(events)

        loaded = load_payment_events(db, "141air", "2026-01-01", "2026-01-31")
        movs = events_to_payment_movements(loaded)

        # Must have exactly one release group, dated at release_date, NET=+39.40
        release_movs = [m for m in movs if m.ref_id == "138580747200"]
        assert len(release_movs) == 1, f"Expected 1 release movement, got {len(release_movs)}"
        assert release_movs[0].date == jan
        assert release_movs[0].amount == Decimal("39.40")
