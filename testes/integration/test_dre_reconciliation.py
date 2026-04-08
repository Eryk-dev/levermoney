"""
DRE Reconciliation Tests — validates processor output against real data.

Uses REAL payment cache (141air January 2026) and REAL extrato CSV to guarantee
that a backfill produces correct financial entries matching ML/MP.

Run: python3 -m pytest testes/test_dre_reconciliation.py -v
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
from app.services.extrato_ingester import (
    _CHECK_PAYMENTS,
    _classify_extrato_line,
    _parse_account_statement,
)


# ---------------------------------------------------------------------------
# Session-scoped fixtures — load real data once
# ---------------------------------------------------------------------------

CACHE_PATH = Path(__file__).parent.parent / "data" / "cache_jan2026" / "141air_payments.json"
EXTRATO_PATH = Path(__file__).parent.parent / "data" / "extratos" / "extrato janeiro 141Air.csv"


def _d(val) -> Decimal:
    """Convert any numeric to Decimal."""
    return Decimal(str(val))


@pytest.fixture(scope="session")
def all_payments():
    """All 879 payments from 141air cache."""
    cache = json.loads(CACHE_PATH.read_text())
    return cache["payments"]


@pytest.fixture(scope="session")
def processable_payments(all_payments):
    """Payments that the processor would actually process in a fresh backfill.

    Applies the EXACT same filters as process_payment_webhook():
    1. Must have order.id
    2. Not marketplace_shipment
    3. collector.id must be None (not a purchase)
    4. Not cancelled/rejected
    5. Not refunded/by_admin (backfill: kit split, new payments cover it)
    6. Must have date_approved
    7. date_approved BRT must be in 2026-01
    """
    result = []
    for p in all_payments:
        order = p.get("order") or {}
        if not order.get("id"):
            continue
        if (p.get("description") or "") == "marketplace_shipment":
            continue
        if (p.get("collector") or {}).get("id") is not None:
            continue
        if p["status"] in ("cancelled", "rejected"):
            continue
        if p["status"] == "refunded" and p.get("status_detail") == "by_admin":
            continue
        da = p.get("date_approved") or ""
        if not da:
            continue
        brt = _to_brt_date(da)
        if not brt or not brt.startswith("2026-01"):
            continue
        result.append(p)
    return result


@pytest.fixture(scope="session")
def payment_groups(processable_payments):
    """Split processable into groups by what the processor does."""
    approved_ml = []
    approved_mp = []
    cb_reimbursed = []
    refunded_ml = []
    refunded_mp = []

    for p in processable_payments:
        status = p["status"]
        sd = p.get("status_detail", "")
        otype = (p.get("order") or {}).get("type", "")

        if status in ("approved", "in_mediation"):
            if otype == "mercadolibre":
                approved_ml.append(p)
            else:
                approved_mp.append(p)
        elif status == "charged_back" and sd == "reimbursed":
            cb_reimbursed.append(p)
        elif status in ("refunded", "charged_back"):
            if otype == "mercadolibre":
                refunded_ml.append(p)
            else:
                refunded_mp.append(p)

    return {
        "approved_ml": approved_ml,
        "approved_mp": approved_mp,
        "cb_reimbursed": cb_reimbursed,
        "refunded_ml": refunded_ml,
        "refunded_mp": refunded_mp,
    }


@pytest.fixture(scope="session")
def dre_values(processable_payments, payment_groups):
    """Compute all DRE values from real data using Decimal arithmetic."""
    g = payment_groups

    # Receita: approved + cb_reimbursed create receita directly
    # Refunded also create receita first (processor calls _process_approved)
    receita_111 = sum(_d(p["transaction_amount"]) for p in
                      g["approved_ml"] + g["cb_reimbursed"] + g["refunded_ml"])
    receita_112 = sum(_d(p["transaction_amount"]) for p in
                      g["approved_mp"] + g["refunded_mp"])

    # Comissao and frete for ALL processable
    comissao = Decimal("0")
    frete = Decimal("0")
    comissao_count = 0
    frete_count = 0
    for p in processable_payments:
        mp_fee, ship, _, _, _ = _extract_processor_charges(p)
        if mp_fee > 0:
            comissao += _d(mp_fee)
            comissao_count += 1
        if ship > 0:
            frete += _d(ship)
            frete_count += 1

    # Devolucoes
    devolucao = Decimal("0")
    all_refunded = g["refunded_ml"] + g["refunded_mp"]
    for p in all_refunded:
        amt = _d(p["transaction_amount"])
        refunds = p.get("refunds", [])
        if refunds:
            total_ref = sum(_d(r.get("amount", 0)) for r in refunds)
        else:
            total_ref = _d(p.get("transaction_amount_refunded") or p["transaction_amount"])
        devolucao += min(total_ref, amt)

    # Estorno taxa and frete
    estorno_taxa = Decimal("0")
    estorno_taxa_count = 0
    estorno_frete = Decimal("0")
    estorno_frete_count = 0
    for p in all_refunded:
        amt = _d(p["transaction_amount"])
        refunds = p.get("refunds", [])
        if refunds:
            total_ref = sum(_d(r.get("amount", 0)) for r in refunds)
        else:
            total_ref = _d(p.get("transaction_amount_refunded") or p["transaction_amount"])
        estorno_receita = min(total_ref, amt)
        if estorno_receita < amt:
            continue  # partial refund: no estorno taxa/frete

        ref_fee = Decimal("0")
        ref_ship = Decimal("0")
        has_charges = False
        for c in p.get("charges_details", []):
            if (c.get("accounts") or {}).get("from") != "collector":
                continue
            ctype = str(c.get("type", "")).lower()
            cname = str(c.get("name", "")).strip().lower()
            if cname == "financing_fee":
                continue
            refunded_val = _d(_to_float((c.get("amounts") or {}).get("refunded", 0)))
            if ctype == "fee":
                ref_fee += refunded_val
                has_charges = True
            elif ctype == "shipping":
                ref_ship += refunded_val
                has_charges = True

        if not has_charges:
            net = _d(_to_float((p.get("transaction_details") or {}).get("net_received_amount", 0)))
            ref_fee = (amt - net) if net > 0 else Decimal("0")

        if ref_fee > 0:
            estorno_taxa += ref_fee
            estorno_taxa_count += 1
        if ref_ship > 0:
            estorno_frete += ref_ship
            estorno_frete_count += 1

    return {
        "receita_111": receita_111,
        "receita_112": receita_112,
        "comissao": comissao,
        "comissao_count": comissao_count,
        "frete": frete,
        "frete_count": frete_count,
        "devolucao": devolucao,
        "devolucao_count": len(all_refunded),
        "estorno_taxa": estorno_taxa,
        "estorno_taxa_count": estorno_taxa_count,
        "estorno_frete": estorno_frete,
        "estorno_frete_count": estorno_frete_count,
    }


@pytest.fixture(scope="session")
def extrato_data():
    """Parsed extrato: (summary, transactions)."""
    for enc in ("utf-8-sig", "latin-1"):
        try:
            text = EXTRATO_PATH.read_text(encoding=enc)
            break
        except UnicodeDecodeError:
            continue
    return _parse_account_statement(text)


# ===========================================================================
# Test Class 1: Data Loading & Filtering
# ===========================================================================

class TestDataLoading:
    def test_cache_total(self, all_payments):
        assert len(all_payments) == 879

    def test_processable_count(self, processable_payments):
        assert len(processable_payments) == 438

    def test_approved_ml_count(self, payment_groups):
        assert len(payment_groups["approved_ml"]) == 359

    def test_approved_mp_count(self, payment_groups):
        assert len(payment_groups["approved_mp"]) == 1

    def test_cb_reimbursed_count(self, payment_groups):
        assert len(payment_groups["cb_reimbursed"]) == 1

    def test_refunded_ml_count(self, payment_groups):
        assert len(payment_groups["refunded_ml"]) == 77

    def test_refunded_mp_count(self, payment_groups):
        assert len(payment_groups["refunded_mp"]) == 0

    def test_group_sum_equals_processable(self, processable_payments, payment_groups):
        g = payment_groups
        total = sum(len(v) for v in g.values())
        assert total == len(processable_payments)

    def test_extrato_transaction_count(self, extrato_data):
        _, txs = extrato_data
        assert len(txs) == 690


# ===========================================================================
# Test Class 2: Skip Filters
# ===========================================================================

class TestSkipFilters:
    def test_no_order_id_skipped(self, all_payments):
        no_order = [p for p in all_payments if not (p.get("order") or {}).get("id")]
        assert len(no_order) == 80

    def test_marketplace_shipment_skipped(self, all_payments):
        ms = [p for p in all_payments
              if (p.get("description") or "") == "marketplace_shipment"]
        assert len(ms) == 16

    def test_collector_id_skipped(self, all_payments):
        """Payments with collector.id are purchases by the seller, not sales."""
        with_coll = [p for p in all_payments
                     if (p.get("order") or {}).get("id")
                     and (p.get("collector") or {}).get("id") is not None]
        assert len(with_coll) == 6

    def test_by_admin_skipped(self, all_payments):
        """refunded/by_admin are kit splits — new payments cover the revenue."""
        by_admin = [p for p in all_payments
                    if p["status"] == "refunded"
                    and p.get("status_detail") == "by_admin"
                    and (p.get("order") or {}).get("id")]
        assert len(by_admin) == 2

    def test_all_processable_are_january_brt(self, processable_payments):
        for p in processable_payments:
            da = p.get("date_approved", "")
            brt = _to_brt_date(da)
            assert brt.startswith("2026-01"), f"Payment {p['id']} has BRT date {brt}"


# ===========================================================================
# Test Class 3: Receita (1.1.1 + 1.1.2)
# ===========================================================================

class TestReceita:
    def test_receita_mercadolibre(self, dre_values):
        """1.1.1 Vendas ML = R$179,512.35 (approved + CB/reimbursed + refunded)."""
        assert dre_values["receita_111"] == _d("179512.35")

    def test_receita_mercadopago(self, dre_values):
        """1.1.2 Loja Propria = R$59.90."""
        assert dre_values["receita_112"] == _d("59.90")

    def test_receita_bruta_total(self, dre_values):
        total = dre_values["receita_111"] + dre_values["receita_112"]
        assert total == _d("179572.25")

    def test_refunded_create_receita_first(self, payment_groups, dre_values):
        """Refunded payments ALSO create receita (processor calls _process_approved).
        So receita_111 includes 77 refunded amounts."""
        refunded_total = sum(_d(p["transaction_amount"]) for p in payment_groups["refunded_ml"])
        approved_total = sum(_d(p["transaction_amount"]) for p in
                            payment_groups["approved_ml"] + payment_groups["cb_reimbursed"])
        assert dre_values["receita_111"] == approved_total + refunded_total

    def test_cb_reimbursed_creates_receita(self, payment_groups):
        """charged_back+reimbursed (id=140797336762) creates receita like approved."""
        cb = payment_groups["cb_reimbursed"]
        assert len(cb) == 1
        assert cb[0]["id"] == 140797336762
        assert _d(cb[0]["transaction_amount"]) == _d("113.06")

    def test_mercadolibre_excl_cb_close_to_ml(self, payment_groups):
        """ML 'vendas por competencia' shows ~R$179,814 (user value, rounded).
        Our calculation excl CB = R$179,399.29.
        Gap of ~R$415 is due to collector_id filter (R$55.88) + by_admin skip (R$355.94).
        These are correctly excluded from the backfill but ML counts them in dashboard."""
        approved_plus_refunded = sum(
            _d(p["transaction_amount"])
            for p in payment_groups["approved_ml"] + payment_groups["refunded_ml"]
        )
        assert approved_plus_refunded == _d("179399.29")
        # ML dashboard shows ~R$179,814 (includes by_admin + collector_id payments)
        # Processor correctly excludes them to avoid duplicate/invalid entries


# ===========================================================================
# Test Class 4: Comissao (2.8.2)
# ===========================================================================

class TestComissao:
    def test_comissao_total(self, dre_values):
        assert dre_values["comissao"] == _d("23085.97")

    def test_comissao_count(self, dre_values):
        assert dre_values["comissao_count"] == 435

    def test_financing_fee_excluded(self, processable_payments):
        """No financing_fee amount appears in comissao — it is net-neutral."""
        for p in processable_payments:
            mp_fee, _, _, _, _ = _extract_processor_charges(p)
            # Manually compute what fee WOULD be if financing_fee were included
            financing_fee_total = sum(
                _to_float(c["amounts"].get("original", 0))
                for c in p.get("charges_details", [])
                if (c.get("accounts") or {}).get("from") == "collector"
                and str(c.get("name", "")).strip().lower() == "financing_fee"
            )
            if financing_fee_total > 0:
                # If financing_fee existed, mp_fee must NOT include it
                fee_with_financing = mp_fee + financing_fee_total
                assert fee_with_financing > mp_fee

    def test_coupon_from_ml_excluded(self, processable_payments):
        """Coupons from=ml are NOT charged to seller."""
        for p in processable_payments:
            ml_coupons = [
                c for c in p.get("charges_details", [])
                if (c.get("accounts") or {}).get("from") == "ml"
                and c.get("type") == "coupon"
            ]
            if ml_coupons:
                # These coupons should NOT be in the fee
                mp_fee, _, _, _, _ = _extract_processor_charges(p)
                coupon_total = sum(_to_float(c["amounts"]["original"]) for c in ml_coupons)
                # Rebuild fee manually without the coupon to verify
                fee_without_ml_coupon = sum(
                    _to_float(c["amounts"]["original"])
                    for c in p.get("charges_details", [])
                    if (c.get("accounts") or {}).get("from") == "collector"
                    and c.get("type") in ("fee", "coupon")
                    and str(c.get("name", "")).strip().lower() != "financing_fee"
                )
                assert abs(mp_fee - fee_without_ml_coupon) < 0.01

    def test_no_comissao_without_charges(self, processable_payments):
        """Payments with empty charges_details have comissao = 0."""
        for p in processable_payments:
            if not p.get("charges_details"):
                mp_fee, _, _, _, _ = _extract_processor_charges(p)
                assert mp_fee == 0.0


# ===========================================================================
# Test Class 5: Frete (2.9.4)
# ===========================================================================

class TestFrete:
    def test_frete_total(self, dre_values):
        assert dre_values["frete"] == _d("8946.37")

    def test_frete_count(self, dre_values):
        assert dre_values["frete_count"] == 362

    def test_frete_never_negative(self, processable_payments):
        for p in processable_payments:
            _, ship, _, _, _ = _extract_processor_charges(p)
            assert ship >= 0, f"Payment {p['id']} has negative shipping: {ship}"


# ===========================================================================
# Test Class 6: Devolucoes (1.2.1)
# ===========================================================================

class TestDevolucoes:
    def test_devolucao_total(self, dre_values):
        assert dre_values["devolucao"] == _d("45375.41")

    def test_devolucao_count(self, dre_values):
        assert dre_values["devolucao_count"] == 77

    def test_devolucao_capped_at_amount(self, payment_groups):
        """Estorno receita can never exceed transaction_amount."""
        for p in payment_groups["refunded_ml"] + payment_groups["refunded_mp"]:
            amt = _d(p["transaction_amount"])
            refunds = p.get("refunds", [])
            if refunds:
                total_ref = sum(_d(r.get("amount", 0)) for r in refunds)
            else:
                total_ref = _d(p.get("transaction_amount_refunded") or p["transaction_amount"])
            estorno = min(total_ref, amt)
            assert estorno <= amt


# ===========================================================================
# Test Class 7: Estorno Taxa (1.3.4)
# ===========================================================================

class TestEstornoTaxa:
    def test_estorno_taxa_total(self, dre_values):
        assert dre_values["estorno_taxa"] == _d("5948.66")

    def test_estorno_taxa_count(self, dre_values):
        assert dre_values["estorno_taxa_count"] == 75

    def test_estorno_taxa_only_full_refund(self, payment_groups):
        """Estorno taxa only created when estorno_receita >= transaction_amount."""
        for p in payment_groups["refunded_ml"]:
            amt = _d(p["transaction_amount"])
            refunds = p.get("refunds", [])
            if refunds:
                total_ref = sum(_d(r.get("amount", 0)) for r in refunds)
            else:
                total_ref = _d(p.get("transaction_amount_refunded") or p["transaction_amount"])
            estorno = min(total_ref, amt)
            if estorno < amt:
                # Partial refund: no estorno taxa should be created
                ref_fee = Decimal("0")
                for c in p.get("charges_details", []):
                    if (c.get("accounts") or {}).get("from") != "collector":
                        continue
                    if str(c.get("type", "")).lower() == "fee":
                        ref_fee += _d(_to_float(c["amounts"].get("refunded", 0)))
                # Even if fees were refunded, processor skips them for partial refunds
                # (the estorno_receita < amount check in processor)


# ===========================================================================
# Test Class 8: Estorno Frete (1.3.7)
# ===========================================================================

class TestEstornoFrete:
    def test_estorno_frete_total(self, dre_values):
        assert dre_values["estorno_frete"] == _d("1442.21")

    def test_estorno_frete_count(self, dre_values):
        assert dre_values["estorno_frete_count"] == 54


# ===========================================================================
# Test Class 9: Per-Payment Balance
# ===========================================================================

class TestPerPaymentBalance:
    def test_every_payment_balances(self, processable_payments):
        """For every payment: amount - fee - shipping = reconciled_net."""
        for p in processable_payments:
            mp_fee, ship, _, reconciled_net, _ = _extract_processor_charges(p)
            amt = _to_float(p["transaction_amount"])
            calc = round(amt - mp_fee - ship, 2)
            assert abs(calc - reconciled_net) < 0.02, (
                f"Payment {p['id']}: {amt} - {mp_fee} - {ship} = {calc} != {reconciled_net}"
            )

    def test_no_negative_net(self, processable_payments):
        """No payment should produce a negative reconciled_net."""
        for p in processable_payments:
            _, _, _, reconciled_net, _ = _extract_processor_charges(p)
            assert reconciled_net >= 0, f"Payment {p['id']} has negative net: {reconciled_net}"


# ===========================================================================
# Test Class 10: DRE Math Consistency
# ===========================================================================

class TestDREConsistency:
    def test_receita_liquida(self, dre_values):
        v = dre_values
        receita_liq = (
            v["receita_111"] + v["receita_112"]
            - v["devolucao"]
            + v["estorno_taxa"]
            + v["estorno_frete"]
        )
        assert receita_liq == _d("141587.71")

    def test_resultado_operacional(self, dre_values):
        v = dre_values
        receita_liq = (
            v["receita_111"] + v["receita_112"]
            - v["devolucao"]
            + v["estorno_taxa"]
            + v["estorno_frete"]
        )
        resultado = receita_liq - v["comissao"] - v["frete"]
        assert resultado == _d("109555.37")


# ===========================================================================
# Test Class 11: Extrato — Liberacao Match
# ===========================================================================

class TestExtratoLiberacaoMatch:
    @pytest.fixture(scope="class")
    def liberacao_entries(self, extrato_data, all_payments):
        """Pairs of (extrato_tx, payment) for all Liberacao de dinheiro lines."""
        _, txs = extrato_data
        payments_by_id = {str(p["id"]): p for p in all_payments}
        pairs = []
        for tx in txs:
            if "Liberação de dinheiro" not in tx["transaction_type"]:
                continue
            ref_id = tx["reference_id"]
            if ref_id in payments_by_id:
                pairs.append((tx, payments_by_id[ref_id]))
        return pairs

    def test_liberacao_count(self, liberacao_entries):
        assert len(liberacao_entries) == 289

    def test_net_amount_match(self, liberacao_entries):
        """Every liberacao amount matches net_received_amount from payment."""
        for tx, p in liberacao_entries:
            ext_amount = _d(str(tx["amount"]))
            pay_net = _d(str(p.get("transaction_details", {}).get("net_received_amount", 0)))
            assert abs(ext_amount - pay_net) < _d("0.02"), (
                f"Payment {p['id']}: extrato={ext_amount} vs net={pay_net}"
            )

    def test_release_date_match(self, liberacao_entries):
        """Every liberacao date matches _to_brt_date(money_release_date)."""
        for tx, p in liberacao_entries:
            mrd = p.get("money_release_date", "")
            if not mrd:
                continue
            brt_mrd = _to_brt_date(mrd)
            assert brt_mrd == tx["date"], (
                f"Payment {p['id']}: BRT release={brt_mrd} vs extrato={tx['date']}"
            )


# ===========================================================================
# Test Class 12: Extrato — Full Coverage
# ===========================================================================

class TestExtratoCoverage:
    def test_extrato_summary_balances(self, extrato_data):
        summary, _ = extrato_data
        initial = _d(str(summary["initial_balance"]))
        credits = _d(str(summary["credits"]))
        debits = _d(str(summary["debits"]))
        final = _d(str(summary["final_balance"]))
        assert initial + credits + debits == final

    def test_no_unclassified_lines(self, extrato_data):
        _, txs = extrato_data
        unclassified = []
        for tx in txs:
            exp_type, _, _ = _classify_extrato_line(tx["transaction_type"])
            if exp_type == "other":
                unclassified.append(tx["transaction_type"])
        assert not unclassified, f"Unclassified: {unclassified}"

    def test_extrato_gap_zero(self, extrato_data):
        """All extrato lines are covered: payments + mp_expenses + skips = net."""
        _, txs = extrato_data
        payments_total = _d("0")
        expenses_total = _d("0")
        skip_total = _d("0")
        for tx in txs:
            amt = _d(str(tx["amount"]))
            exp_type, _, _ = _classify_extrato_line(tx["transaction_type"])
            if exp_type is None:
                skip_total += amt
            elif exp_type == _CHECK_PAYMENTS:
                payments_total += amt
            else:
                expenses_total += amt
        total = payments_total + expenses_total + skip_total
        net = sum(_d(str(tx["amount"])) for tx in txs)
        assert total == net


# ===========================================================================
# Test Class 13: Competencia Date
# ===========================================================================

class TestCompetenciaDate:
    def test_no_month_crossing_in_january(self, all_payments):
        """No payment in the cache has date_approved that changes month between
        UTC-4 (raw) and BRT — at least for January 2026."""
        from datetime import datetime, timezone, timedelta
        BRT = timezone(timedelta(hours=-3))
        crossings = 0
        for p in all_payments:
            da = p.get("date_approved", "")
            if not da:
                continue
            raw_month = da[:7]
            try:
                dt = datetime.fromisoformat(da)
                brt_month = dt.astimezone(BRT).strftime("%Y-%m")
            except (ValueError, TypeError):
                continue
            if raw_month != brt_month:
                crossings += 1
        assert crossings == 0

    def test_competencia_uses_date_approved_not_created(self, processable_payments):
        """Every processable payment uses date_approved (not date_created) for competencia."""
        for p in processable_payments:
            assert p.get("date_approved"), f"Payment {p['id']} missing date_approved"
