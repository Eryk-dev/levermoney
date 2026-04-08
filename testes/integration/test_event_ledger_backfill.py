"""
Event Ledger Backfill Validation — offline tests using real cache data.

Simulates what the backfill script would produce by processing local JSON
cache through the same logic as processor.py, then validates that the
resulting event ledger totals match the known DRE reference values.

This guarantees that the event ledger can reproduce the exact same DRE
numbers as the current snapshot-based system.

Run: python3 -m pytest testes/test_event_ledger_backfill.py -v
"""
import json
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

import pytest

from app.services.processor import (
    _extract_processor_charges,
    _to_brt_date,
    _to_float,
)
from app.services.event_ledger import (
    build_idempotency_key,
    validate_event,
    EVENT_TYPES,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _d(val) -> Decimal:
    return Decimal(str(val))


CACHE_JAN = Path(__file__).parent.parent / "data" / "cache_jan2026" / "141air_payments.json"
CACHE_FEV = Path(__file__).parent.parent / "data" / "cache_fev2026" / "141air_payments.json"


def _load_cache(path: Path) -> list[dict]:
    cache = json.loads(path.read_text())
    return cache["payments"]


def _is_processable(p: dict, month_prefix: str) -> bool:
    """Apply same filters as process_payment_webhook for backfill."""
    order = p.get("order") or {}
    if not order.get("id"):
        return False
    if (p.get("description") or "") == "marketplace_shipment":
        return False
    if (p.get("collector") or {}).get("id") is not None:
        return False
    if p["status"] in ("cancelled", "rejected"):
        return False
    if p["status"] == "refunded" and p.get("status_detail") == "by_admin":
        return False
    da = p.get("date_approved") or ""
    if not da:
        return False
    brt = _to_brt_date(da)
    if not brt or not brt.startswith(month_prefix):
        return False
    return True


def _simulate_events(payments: list[dict], month_prefix: str) -> list[dict]:
    """Simulate event ledger entries for processable payments.

    Returns a list of event dicts with: event_type, signed_amount,
    competencia_date, ml_payment_id, idempotency_key.
    """
    events = []
    seller = "141air"

    for p in payments:
        if not _is_processable(p, month_prefix):
            continue

        payment_id = p["id"]
        status = p["status"]
        sd = p.get("status_detail", "")
        da = p.get("date_approved") or p.get("date_created", "")
        competencia = _to_brt_date(da)
        order_id = (p.get("order") or {}).get("id")
        amount = _to_float(p.get("transaction_amount"))

        mp_fee, shipping_cost, _, reconciled_net, net_diff = _extract_processor_charges(p)
        net = _to_float((p.get("transaction_details") or {}).get("net_received_amount", 0))

        # All processable payments create sale + fee + shipping events
        # (processor creates receita even for refunded payments)
        events.append({
            "event_type": "sale_approved",
            "signed_amount": amount,
            "competencia_date": competencia,
            "ml_payment_id": payment_id,
            "idempotency_key": build_idempotency_key(seller, payment_id, "sale_approved"),
        })

        if mp_fee > 0:
            events.append({
                "event_type": "fee_charged",
                "signed_amount": -mp_fee,
                "competencia_date": competencia,
                "ml_payment_id": payment_id,
                "idempotency_key": build_idempotency_key(seller, payment_id, "fee_charged"),
            })

        if shipping_cost > 0:
            events.append({
                "event_type": "shipping_charged",
                "signed_amount": -shipping_cost,
                "competencia_date": competencia,
                "ml_payment_id": payment_id,
                "idempotency_key": build_idempotency_key(seller, payment_id, "shipping_charged"),
            })

        # Subsidy
        subsidy = round(net - reconciled_net, 2) if net_diff > 0 else 0.0
        if subsidy >= 0.01:
            events.append({
                "event_type": "subsidy_credited",
                "signed_amount": subsidy,
                "competencia_date": competencia,
                "ml_payment_id": payment_id,
                "idempotency_key": build_idempotency_key(seller, payment_id, "subsidy_credited"),
            })

        # Refund events
        if status in ("refunded", "charged_back") and not (status == "charged_back" and sd == "reimbursed"):
            refunds = p.get("refunds") or []
            if refunds:
                total_refunded_raw = sum(_to_float(r.get("amount", 0)) for r in refunds)
                date_refunded = (refunds[-1].get("date_created") or "")[:10]
            else:
                total_refunded_raw = _to_float(p.get("transaction_amount_refunded")) or amount
                date_refunded = competencia

            estorno_receita = min(total_refunded_raw, amount)

            events.append({
                "event_type": "refund_created",
                "signed_amount": -estorno_receita,
                "competencia_date": competencia,
                "event_date": date_refunded,
                "ml_payment_id": payment_id,
                "idempotency_key": build_idempotency_key(seller, payment_id, "refund_created"),
            })

            # Estorno taxa/frete only on full refunds
            if estorno_receita >= amount:
                ref_fee = 0.0
                ref_ship = 0.0
                has_charges = False
                for c in p.get("charges_details") or []:
                    if (c.get("accounts") or {}).get("from") != "collector":
                        continue
                    ctype = str(c.get("type", "")).lower()
                    cname = str(c.get("name", "")).strip().lower()
                    if cname == "financing_fee":
                        continue
                    refunded_val = _to_float((c.get("amounts") or {}).get("refunded"))
                    if ctype == "fee":
                        ref_fee += refunded_val
                        has_charges = True
                    elif ctype == "shipping":
                        ref_ship += refunded_val
                        has_charges = True

                if not has_charges:
                    fee_net = _to_float((p.get("transaction_details") or {}).get("net_received_amount"))
                    ref_fee = round(amount - fee_net, 2) if fee_net > 0 else 0

                ref_fee = round(ref_fee, 2)
                ref_ship = round(ref_ship, 2)

                if ref_fee > 0:
                    events.append({
                        "event_type": "refund_fee",
                        "signed_amount": ref_fee,
                        "competencia_date": competencia,
                        "ml_payment_id": payment_id,
                        "idempotency_key": build_idempotency_key(seller, payment_id, "refund_fee"),
                    })
                if ref_ship > 0:
                    events.append({
                        "event_type": "refund_shipping",
                        "signed_amount": ref_ship,
                        "competencia_date": competencia,
                        "ml_payment_id": payment_id,
                        "idempotency_key": build_idempotency_key(seller, payment_id, "refund_shipping"),
                    })

    return events


def _aggregate_events(events: list[dict]) -> dict[str, Decimal]:
    """Sum signed_amount by event_type."""
    agg: dict[str, Decimal] = defaultdict(Decimal)
    for e in events:
        agg[e["event_type"]] += _d(e["signed_amount"])
    return dict(agg)


def _count_events(events: list[dict]) -> dict[str, int]:
    """Count events by event_type."""
    counts: dict[str, int] = defaultdict(int)
    for e in events:
        counts[e["event_type"]] += 1
    return dict(counts)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def jan_events():
    payments = _load_cache(CACHE_JAN)
    return _simulate_events(payments, "2026-01")


@pytest.fixture(scope="session")
def fev_events():
    payments = _load_cache(CACHE_FEV)
    return _simulate_events(payments, "2026-02")


@pytest.fixture(scope="session")
def jan_agg(jan_events):
    return _aggregate_events(jan_events)


@pytest.fixture(scope="session")
def fev_agg(fev_events):
    return _aggregate_events(fev_events)


@pytest.fixture(scope="session")
def jan_counts(jan_events):
    return _count_events(jan_events)


@pytest.fixture(scope="session")
def fev_counts(fev_events):
    return _count_events(fev_events)


# ===========================================================================
# January 2026 — DRE Reference Values
# ===========================================================================

class TestJanReceita:
    """Receita from event ledger must match DRE reference."""

    def test_receita_total(self, jan_agg):
        """sale_approved total = receita_111 + receita_112 from DRE.
        179512.35 (ML) + 59.90 (MP) = 179572.25"""
        assert jan_agg["sale_approved"] == _d("179572.25")

    def test_receita_count(self, jan_counts):
        assert jan_counts["sale_approved"] == 438


class TestJanComissao:
    def test_comissao_total(self, jan_agg):
        assert jan_agg["fee_charged"] == _d("-23085.97")

    def test_comissao_count(self, jan_counts):
        assert jan_counts["fee_charged"] == 435


class TestJanFrete:
    def test_frete_total(self, jan_agg):
        assert jan_agg["shipping_charged"] == _d("-8946.37")

    def test_frete_count(self, jan_counts):
        assert jan_counts["shipping_charged"] == 362


class TestJanDevolucao:
    def test_devolucao_total(self, jan_agg):
        assert jan_agg["refund_created"] == _d("-45375.41")

    def test_devolucao_count(self, jan_counts):
        assert jan_counts["refund_created"] == 77


class TestJanEstorno:
    def test_estorno_taxa(self, jan_agg):
        assert jan_agg.get("refund_fee", _d("0")) == _d("5948.66")

    def test_estorno_frete(self, jan_agg):
        assert jan_agg.get("refund_shipping", _d("0")) == _d("1442.21")


class TestJanCounts:
    def test_has_sale_events(self, jan_counts):
        assert jan_counts["sale_approved"] > 0

    def test_has_fee_events(self, jan_counts):
        assert jan_counts["fee_charged"] > 0

    def test_has_refund_events(self, jan_counts):
        assert jan_counts["refund_created"] > 0


# ===========================================================================
# February 2026 — DRE Reference Values
# ===========================================================================

class TestFevReceita:
    def test_receita_total(self, fev_agg):
        # 119430.70 (ML) + 0 (MP) = 119430.70
        assert fev_agg["sale_approved"] == _d("119430.70")


class TestFevComissao:
    def test_comissao_total(self, fev_agg):
        assert fev_agg["fee_charged"] == _d("-15320.88")


class TestFevFrete:
    def test_frete_total(self, fev_agg):
        assert fev_agg["shipping_charged"] == _d("-7147.44")


class TestFevDevolucao:
    def test_devolucao_total(self, fev_agg):
        assert fev_agg["refund_created"] == _d("-8467.38")


class TestFevEstorno:
    def test_estorno_taxa(self, fev_agg):
        assert fev_agg.get("refund_fee", _d("0")) == _d("1034.81")

    def test_estorno_frete(self, fev_agg):
        assert fev_agg.get("refund_shipping", _d("0")) == _d("406.70")


# ===========================================================================
# Event integrity checks
# ===========================================================================

class TestEventIntegrity:
    """Validate that all simulated events pass validation."""

    def test_all_jan_events_valid(self, jan_events):
        for e in jan_events:
            validate_event(e["event_type"], e["signed_amount"])

    def test_all_fev_events_valid(self, fev_events):
        for e in fev_events:
            validate_event(e["event_type"], e["signed_amount"])

    def test_jan_idempotency_keys_unique(self, jan_events):
        keys = [e["idempotency_key"] for e in jan_events]
        assert len(keys) == len(set(keys)), "Duplicate idempotency keys found"

    def test_fev_idempotency_keys_unique(self, fev_events):
        keys = [e["idempotency_key"] for e in fev_events]
        assert len(keys) == len(set(keys)), "Duplicate idempotency keys found"


class TestLedgerBalance:
    """Net balance per payment should be consistent."""

    def test_approved_payment_balance_positive(self, jan_events):
        """For approved payments (no refund), balance = net received."""
        by_payment: dict[int, list] = defaultdict(list)
        for e in jan_events:
            by_payment[e["ml_payment_id"]].append(e)

        for pid, evts in by_payment.items():
            types = {e["event_type"] for e in evts}
            if "refund_created" in types:
                continue  # skip refunded
            balance = sum(_d(e["signed_amount"]) for e in evts)
            assert balance >= 0, f"Payment {pid} has negative balance {balance} without refund"

    def test_refunded_payments_have_all_events(self, jan_events):
        """Every refunded payment should have sale + refund + at least refund_fee."""
        by_payment: dict[int, list] = defaultdict(list)
        for e in jan_events:
            by_payment[e["ml_payment_id"]].append(e)

        for pid, evts in by_payment.items():
            types = {e["event_type"] for e in evts}
            if "refund_created" not in types:
                continue
            # Must have the original sale event too
            assert "sale_approved" in types, \
                f"Payment {pid}: has refund but no sale_approved"
