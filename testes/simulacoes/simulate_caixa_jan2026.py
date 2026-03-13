#!/usr/bin/env python3
"""
DRE Simulacao — Janeiro 2026 (Caixa / Vencimento)
==================================================
DRE usando regime de caixa (money_release_date):
  - Receita reconhecida quando money_release_date cai em janeiro
  - Despesas (comissao, frete) na mesma data de liberacao da venda
  - Inclui vendas aprovadas em DEZ/2025 liberadas em JAN/2026
  - Exclui vendas aprovadas em JAN/2026 liberadas em FEV/2026+

Diferenca vs Competencia:
  Competencia = data_approved (quando a venda foi aprovada)
  Caixa       = money_release_date (quando o dinheiro foi liberado)

Uso:
    python3 testes/simulate_caixa_jan2026.py
    python3 testes/simulate_caixa_jan2026.py --seller net-air
    python3 testes/simulate_caixa_jan2026.py --all
"""

import sys
import json
import logging
import argparse
from pathlib import Path
from datetime import date, datetime, timedelta, timezone
from collections import defaultdict

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.services.processor import _to_brt_date, _to_float, _compute_effective_net_amount
from app.services.expense_classifier import _classify
from app.services.extrato_ingester import _classify_extrato_line, _normalize_text

logging.basicConfig(level=logging.WARNING, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("simulate_caixa")

JAN_START = "2026-01-01"
JAN_END   = "2026-01-31"

CACHE_DIR   = PROJECT_ROOT / "testes" / "cache_jan2026"
EXTRATOS_DIR = PROJECT_ROOT / "testes" / "extratos"

SELLER_EXTRATO_MAP = {
    "141air":          "extrato janeiro 141Air.csv",
    "net-air":         "extrato janeiro netair.csv",
    "netparts-sp":     "extrato janeiro netparts.csv",
    "easy-utilidades": "extrato janeiro Easyutilidades.csv",
}
ALL_SELLERS = list(SELLER_EXTRATO_MAP.keys())


# ══════════════════════════════════════════════════════════════════════════════
# AUXILIARES
# ══════════════════════════════════════════════════════════════════════════════

def fmt_brl(value: float) -> str:
    negative = value < 0
    abs_val = abs(value)
    formatted = f"{abs_val:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    prefix = "R$ -" if negative else "R$  "
    return f"{prefix}{formatted}"


def parse_br_number(raw: str) -> float:
    if not raw or not raw.strip():
        return 0.0
    cleaned = raw.strip().replace(".", "").replace(",", ".")
    try:
        return float(cleaned)
    except ValueError:
        return 0.0


# ══════════════════════════════════════════════════════════════════════════════
# PARSE DO EXTRATO
# ══════════════════════════════════════════════════════════════════════════════

def parse_extrato(csv_path: Path) -> tuple[dict, list[dict]]:
    with open(csv_path, encoding="utf-8-sig") as f:
        lines = f.readlines()

    summary_parts = lines[1].strip().split(";")
    summary = {
        "initial_balance": parse_br_number(summary_parts[0]),
        "credits":         parse_br_number(summary_parts[1]),
        "debits":          parse_br_number(summary_parts[2]),
        "final_balance":   parse_br_number(summary_parts[3]),
    }

    transactions = []
    for line in lines[4:]:
        line = line.strip()
        if not line:
            continue
        parts = line.split(";")
        if len(parts) < 5:
            continue
        if len(parts) == 5:
            date_str, tx_type, ref_id, amount_str, balance_str = parts
        else:
            date_str   = parts[0]
            balance_str = parts[-1]
            amount_str  = parts[-2]
            ref_id      = parts[-3]
            tx_type     = ";".join(parts[1:-3])
        try:
            tx_date = datetime.strptime(date_str.strip(), "%d-%m-%Y").date()
        except ValueError:
            continue
        transactions.append({
            "date":         tx_date.isoformat(),
            "type":         tx_type.strip(),
            "reference_id": ref_id.strip(),
            "amount":       parse_br_number(amount_str),
            "balance":      parse_br_number(balance_str),
        })
    return summary, transactions


# ══════════════════════════════════════════════════════════════════════════════
# SIMULACAO DOS PAYMENTS (identico ao DRE de competencia)
# ══════════════════════════════════════════════════════════════════════════════

def simulate_approved_calcs(payment: dict) -> dict:
    amount = _to_float(payment.get("transaction_amount"))
    td = payment.get("transaction_details") or {}
    net = _to_float(td.get("net_received_amount"))
    effective_net = _compute_effective_net_amount(payment)

    date_approved_raw  = payment.get("date_approved") or payment.get("date_created", "")
    competencia        = _to_brt_date(date_approved_raw)
    money_release_date = (payment.get("money_release_date") or date_approved_raw)[:10]

    charges = payment.get("charges_details") or []
    shipping_collector = 0.0
    mp_fee = 0.0
    for charge in charges:
        accounts = charge.get("accounts") or {}
        if accounts.get("from") != "collector":
            continue
        charge_amount = _to_float((charge.get("amounts") or {}).get("original", 0))
        charge_type   = charge.get("type")
        if charge_type == "shipping":
            shipping_collector += charge_amount
        elif charge_type == "fee":
            name = (charge.get("name") or "").strip().lower()
            if name == "financing_fee":
                continue
            mp_fee += charge_amount

    shipping_buyer  = _to_float(payment.get("shipping_amount"))
    shipping_seller = round(max(0.0, shipping_collector - shipping_buyer), 2)
    mp_fee          = round(mp_fee, 2)
    reconciled_net  = round(amount - mp_fee - shipping_seller, 2)
    net_diff        = round(net - reconciled_net, 2)
    subsidy         = round(net - reconciled_net, 2) if net_diff > 0 else 0.0

    return {
        "amount": amount, "net": net, "effective_net": effective_net,
        "comissao": mp_fee, "frete": shipping_seller, "subsidy": subsidy,
        "competencia": competencia, "money_release_date": money_release_date,
        "net_diff": net_diff, "reconciled_net": reconciled_net,
    }


def simulate_refunded_calcs(payment: dict) -> dict:
    amount = _to_float(payment.get("transaction_amount"))
    td     = payment.get("transaction_details") or {}
    net    = _to_float(td.get("net_received_amount"))
    refunds = payment.get("refunds") or []

    if refunds:
        total_refunded = sum(_to_float(r.get("amount")) for r in refunds)
        date_refunded  = _to_brt_date(refunds[-1].get("date_created", ""))
    else:
        total_refunded = _to_float(payment.get("transaction_amount_refunded")) or amount
        raw_date       = payment.get("date_last_updated") or payment.get("date_created", "")
        date_refunded  = _to_brt_date(raw_date)

    estorno_receita = min(total_refunded, amount)
    total_fees      = round(amount - net, 2) if net > 0 else 0
    approved        = simulate_approved_calcs(payment)

    return {
        "amount": amount, "net": net,
        "effective_net": _compute_effective_net_amount(payment),
        "comissao": approved["comissao"], "frete": approved["frete"],
        "competencia_original": approved["competencia"],
        "competencia_estorno":  date_refunded,
        "money_release_date":   approved["money_release_date"],
        "estorno_receita":      estorno_receita,
        "estorno_taxa":         total_fees if estorno_receita >= amount else 0,
        "total_refunded_raw":   total_refunded,
    }


def simulate_payment(payment: dict) -> dict:
    pid          = payment["id"]
    status       = payment.get("status", "")
    status_detail = payment.get("status_detail", "")
    order_id     = (payment.get("order") or {}).get("id")
    op_type      = payment.get("operation_type", "")

    base = {
        "payment_id":         pid,
        "order_id":           order_id,
        "ml_status":          status,
        "status_detail":      status_detail,
        "operation_type":     op_type,
        "transaction_amount": _to_float(payment.get("transaction_amount")),
        "money_release_date": (payment.get("money_release_date") or "")[:10],
        "date_approved_raw":  payment.get("date_approved", ""),
    }

    if not order_id:
        exp_type, direction, category, auto, desc = _classify(payment)
        if direction == "skip":
            return {**base, "action": "SKIP", "skip_reason": f"non-order interno ({exp_type})"}
        date_approved_raw = payment.get("date_approved") or payment.get("date_created", "")
        amount = _to_float(payment.get("transaction_amount"))
        return {
            **base, "action": "NON_ORDER",
            "expense_type": exp_type, "direction": direction,
            "category": category, "auto_categorized": auto, "description": desc,
            "amount": amount,
            "net": _to_float((payment.get("transaction_details") or {}).get("net_received_amount")),
            "competencia": _to_brt_date(date_approved_raw),
            "date_created": _to_brt_date(payment.get("date_created", "")),
        }

    if payment.get("description") == "marketplace_shipment":
        return {**base, "action": "SKIP", "skip_reason": "marketplace_shipment"}
    if (payment.get("collector") or {}).get("id") is not None:
        return {**base, "action": "SKIP", "skip_reason": "compra (collector_id)"}

    if status in ("approved", "in_mediation"):
        calcs = simulate_approved_calcs(payment)
        return {**base, "action": "APPROVED", **calcs}

    if status == "charged_back" and status_detail == "reimbursed":
        calcs = simulate_approved_calcs(payment)
        return {**base, "action": "CHARGED_BACK_REIMBURSED", **calcs}

    if status == "refunded" and status_detail == "by_admin":
        return {**base, "action": "SKIP", "skip_reason": "refunded/by_admin (kit split)"}

    if status in ("refunded", "charged_back"):
        calcs = simulate_refunded_calcs(payment)
        return {**base, "action": "REFUNDED", **calcs}

    if status in ("cancelled", "rejected"):
        return {**base, "action": "SKIP", "skip_reason": f"status={status}"}

    return {**base, "action": "PENDENTE", "skip_reason": f"status={status}/{status_detail}"}


# ══════════════════════════════════════════════════════════════════════════════
# CONSTRUCAO DO DRE POR CAIXA (money_release_date)
# ══════════════════════════════════════════════════════════════════════════════

def build_dre_caixa(
    simulated_all: list[dict],
    non_order_jan: list[dict],   # non-orders com date_approved em jan
    extrato_gaps:  list[dict],
) -> dict:
    """
    Constroi DRE de janeiro 2026 pelo criterio CAIXA (money_release_date).
    Inclui: payments com money_release_date em jan (independente de quando aprovados).
    Exclui: payments com money_release_date fora de jan (mesmo que aprovados em jan).
    """

    dre = {
        "venda_ml":            {"total": 0.0, "count": 0},
        "devolucao":           {"total": 0.0, "count": 0},
        "estorno_taxa":        {"total": 0.0, "count": 0},
        "estorno_frete":       {"total": 0.0, "count": 0},
        "cashback_ml":         {"total": 0.0, "count": 0},
        "deposito_avulso":     {"total": 0.0, "count": 0},
        "extrato_gap_income":  {"total": 0.0, "count": 0},
        "comissao_ml":         {"total": 0.0, "count": 0},
        "frete_mercadoenvios": {"total": 0.0, "count": 0},
        "tarifa_pagamento":    {"total": 0.0, "count": 0},
        "subscription_saas":   {"total": 0.0, "count": 0},
        "bill_payment":        {"total": 0.0, "count": 0},
        "collection_ml":       {"total": 0.0, "count": 0},
        "extrato_gap_expense": {"total": 0.0, "count": 0},
    }

    # Tracking cross-month
    cross = {
        "dec_approved_jan_release": [],
        "jan_approved_jan_release": [],
        "jan_approved_feb_release": [],  # para referencia — fora do DRE caixa jan
    }

    # ── ORDERS aprovados com money_release_date em JAN ─────────────────────
    for sim in simulated_all:
        if sim["action"] not in ("APPROVED", "CHARGED_BACK_REIMBURSED"):
            continue

        release = sim.get("money_release_date", "")
        if not release or release[:7] != "2026-01":
            # Conta no cross-month para informacao
            competencia = sim.get("competencia", "")
            if competencia[:7] == "2026-01" and release[:7] >= "2026-02":
                cross["jan_approved_feb_release"].append({
                    "payment_id": sim["payment_id"],
                    "amount": sim.get("amount", 0),
                    "net": sim.get("net", 0),
                    "competencia": competencia,
                    "money_release_date": release,
                })
            continue

        amount   = sim.get("amount", 0.0)
        comissao = sim.get("comissao", 0.0)
        frete    = sim.get("frete", 0.0)
        subsidy  = sim.get("subsidy", 0.0)
        competencia = sim.get("competencia", "")

        dre["venda_ml"]["total"] += amount
        dre["venda_ml"]["count"] += 1

        if comissao > 0:
            dre["comissao_ml"]["total"] += comissao
            dre["comissao_ml"]["count"] += 1
        if frete > 0:
            dre["frete_mercadoenvios"]["total"] += frete
            dre["frete_mercadoenvios"]["count"] += 1
        if subsidy >= 0.01:
            dre["estorno_frete"]["total"] += subsidy
            dre["estorno_frete"]["count"] += 1

        # Cross-month classification
        if competencia[:7] == "2025-12":
            cross["dec_approved_jan_release"].append({
                "payment_id": sim["payment_id"],
                "amount": amount, "net": sim.get("net", 0),
                "competencia": competencia, "money_release_date": release,
            })
        else:
            cross["jan_approved_jan_release"].append({
                "payment_id": sim["payment_id"],
                "amount": amount, "net": sim.get("net", 0),
                "competencia": competencia, "money_release_date": release,
            })

    # ── REFUNDED com money_release_date em JAN ────────────────────────────
    # (receita original + estorno, ambos no DRE de jan)
    for sim in simulated_all:
        if sim["action"] != "REFUNDED":
            continue

        release = sim.get("money_release_date", "")
        if not release or release[:7] != "2026-01":
            continue

        amount          = sim.get("amount", 0.0)
        comissao        = sim.get("comissao", 0.0)
        frete           = sim.get("frete", 0.0)
        estorno_receita = sim.get("estorno_receita", 0.0)
        estorno_taxa    = sim.get("estorno_taxa", 0.0)

        # Receita original (seria recebida em jan, mas foi estornada)
        dre["venda_ml"]["total"] += amount
        dre["venda_ml"]["count"] += 1
        if comissao > 0:
            dre["comissao_ml"]["total"] += comissao
            dre["comissao_ml"]["count"] += 1
        if frete > 0:
            dre["frete_mercadoenvios"]["total"] += frete
            dre["frete_mercadoenvios"]["count"] += 1

        # Estorno
        dre["devolucao"]["total"] += estorno_receita
        dre["devolucao"]["count"] += 1
        if estorno_taxa > 0:
            dre["estorno_taxa"]["total"] += estorno_taxa
            dre["estorno_taxa"]["count"] += 1

    # ── NON-ORDERS com date_approved em JAN (liquidacao imediata) ─────────
    for sim in non_order_jan:
        if sim["action"] != "NON_ORDER":
            continue
        if (sim.get("competencia") or "")[:7] != "2026-01":
            continue

        amount    = abs(sim.get("amount", 0.0))
        direction = sim.get("direction", "")
        exp_type  = sim.get("expense_type", "")

        if direction == "income":
            if exp_type == "cashback":
                dre["cashback_ml"]["total"] += amount
                dre["cashback_ml"]["count"] += 1
            else:
                dre["extrato_gap_income"]["total"] += amount
                dre["extrato_gap_income"]["count"] += 1
        elif direction == "expense":
            if exp_type == "darf":
                dre["tarifa_pagamento"]["total"] += amount
                dre["tarifa_pagamento"]["count"] += 1
            elif exp_type == "subscription":
                dre["subscription_saas"]["total"] += amount
                dre["subscription_saas"]["count"] += 1
            elif exp_type == "collection":
                dre["collection_ml"]["total"] += amount
                dre["collection_ml"]["count"] += 1
            else:
                dre["bill_payment"]["total"] += amount
                dre["bill_payment"]["count"] += 1

    # ── GAPS DO EXTRATO ────────────────────────────────────────────────────
    for gap in extrato_gaps:
        if gap.get("date", "")[:7] != "2026-01":
            continue
        amount    = abs(gap.get("amount", 0.0))
        direction = gap.get("direction")
        exp_type  = gap.get("expense_type", "")

        if direction == "income":
            if exp_type == "deposito_avulso":
                dre["deposito_avulso"]["total"] += amount
                dre["deposito_avulso"]["count"] += 1
            else:
                dre["extrato_gap_income"]["total"] += amount
                dre["extrato_gap_income"]["count"] += 1
        elif direction == "expense":
            if exp_type == "difal":
                dre["tarifa_pagamento"]["total"] += amount
                dre["tarifa_pagamento"]["count"] += 1
            else:
                dre["extrato_gap_expense"]["total"] += amount
                dre["extrato_gap_expense"]["count"] += 1

    return {"dre": dre, "cross": cross}


# ══════════════════════════════════════════════════════════════════════════════
# GAPS DO EXTRATO
# ══════════════════════════════════════════════════════════════════════════════

def classify_extrato_gaps(transactions: list[dict], simulated_all: list[dict]) -> list[dict]:
    sim_by_id = {str(s["payment_id"]): s for s in simulated_all}
    gaps = []
    for tx in transactions:
        ref_id = tx["reference_id"]
        sim = sim_by_id.get(ref_id)
        if sim and sim["action"] != "SKIP":
            continue
        expense_type, direction, ca_cat_uuid = _classify_extrato_line(tx["type"])
        if expense_type is None and direction is None:
            continue
        gaps.append({**tx, "expense_type": expense_type, "direction": direction, "ca_category": ca_cat_uuid})
    return gaps


# ══════════════════════════════════════════════════════════════════════════════
# IMPRESSAO
# ══════════════════════════════════════════════════════════════════════════════

def print_dre_caixa(dre_data: dict, extrato_summary: dict, extrato_transactions: list[dict],
                    simulated_all: list[dict], seller_slug: str) -> None:
    dre   = dre_data["dre"]
    cross = dre_data["cross"]
    W     = 70

    total_receita_bruta = dre["venda_ml"]["total"]
    total_devolucao     = dre["devolucao"]["total"]
    total_estorno_taxa  = dre["estorno_taxa"]["total"]
    total_estorno_frete = dre["estorno_frete"]["total"]
    total_cashback      = dre["cashback_ml"]["total"]
    total_deposito      = dre["deposito_avulso"]["total"]
    total_gap_income    = dre["extrato_gap_income"]["total"]

    total_receitas = (total_receita_bruta - total_devolucao + total_estorno_taxa
                      + total_estorno_frete + total_cashback + total_deposito + total_gap_income)

    total_comissao  = dre["comissao_ml"]["total"]
    total_frete     = dre["frete_mercadoenvios"]["total"]
    total_tarifa    = dre["tarifa_pagamento"]["total"]
    total_sub       = dre["subscription_saas"]["total"]
    total_boleto    = dre["bill_payment"]["total"]
    total_cobranca  = dre["collection_ml"]["total"]
    total_gap_exp   = dre["extrato_gap_expense"]["total"]

    total_despesas = (total_comissao + total_frete + total_tarifa + total_sub
                      + total_boleto + total_cobranca + total_gap_exp)

    resultado = total_receitas - total_despesas

    extrato_total_jan = sum(tx["amount"] for tx in extrato_transactions if tx["date"][:7] == "2026-01")

    def dre_row(label: str, value: float, indent: int = 5) -> None:
        prefix = " " * indent
        label_width = W - indent - 20
        val_str = fmt_brl(value)
        print("|" + f"{prefix}{label:<{label_width}}{val_str:>20}" + "|")

    def dre_note(text: str, indent: int = 8) -> None:
        print("|" + f"{' '*indent}{text:<{W-indent}}" + "|")

    title = f"DRE - {seller_slug.upper()} - JANEIRO 2026 (CAIXA / VENCIMENTO)"
    print()
    print("+" + "=" * W + "+")
    print("|" + f"{title:^{W}}" + "|")
    print("+" + "=" * W + "+")
    print("|" + " " * W + "|")

    # ── RECEITAS ─────────────────────────────────────────────────────────────
    print("|" + f"  {'1. RECEITAS':<{W-2}}" + "|")
    print("|" + " " * W + "|")

    # Contagem total de vendas (aprovados com release jan)
    n_jan_jan = len(cross["jan_approved_jan_release"])
    n_dec_jan = len(cross["dec_approved_jan_release"])
    n_refunded_jan = dre["devolucao"]["count"]

    dre_row("1.1.1  Receita Bruta (MercadoLibre)", total_receita_bruta)
    dre_note(f"({n_jan_jan + n_dec_jan} vendas liberadas em jan/2026 por money_release_date)")
    dre_note(f"  Aprovadas em jan, liberadas jan: {n_jan_jan}")
    dre_note(f"  Aprovadas em dez, liberadas jan: {n_dec_jan}")

    if total_devolucao > 0:
        dre_row("1.2.1  (-) Devolucoes e Cancelamentos", -total_devolucao)
        dre_note(f"({n_refunded_jan} devolucoes com release em jan/2026)")
    if total_estorno_taxa > 0:
        dre_row("1.3.4  (+) Estornos de Taxas", total_estorno_taxa)
        dre_note(f"({dre['estorno_taxa']['count']} estornos de taxa)")
    if total_estorno_frete > 0:
        dre_row("1.3.7  (+) Estorno de Frete / Subsidio ML", total_estorno_frete)
    if total_cashback > 0:
        dre_row("1.3.4  (+) Cashback / Ressarcimento ML", total_cashback)
        dre_note(f"({dre['cashback_ml']['count']} cashbacks)")
    if total_deposito > 0:
        dre_row("1.x.x  (+) Depositos / Aportes Avulsos", total_deposito)
    if total_gap_income > 0:
        dre_row("1.3.x  (+) Outros Creditos (extrato)", total_gap_income)
        dre_note(f"({dre['extrato_gap_income']['count']} creditos do extrato)")

    print("|" + " " * W + "|")
    print("|" + f"{'':5}{'─'*45}{'─'*20}" + "|")
    dre_row("TOTAL RECEITAS LIQUIDAS", total_receitas, indent=5)
    print("|" + " " * W + "|")
    print("|" + "-" * W + "|")
    print("|" + " " * W + "|")

    # ── DESPESAS ─────────────────────────────────────────────────────────────
    print("|" + f"  {'2. DESPESAS':<{W-2}}" + "|")
    print("|" + " " * W + "|")

    dre_row("2.8.2  Comissoes Marketplace", -total_comissao)
    dre_note(f"({dre['comissao_ml']['count']} lancamentos de comissao ML)")
    dre_row("2.9.4  MercadoEnvios (Frete Seller)", -total_frete)
    dre_note(f"({dre['frete_mercadoenvios']['count']} lancamentos de frete)")
    if total_tarifa > 0:
        dre_row("2.2.7  Tarifas / Impostos (DIFAL)", -total_tarifa)
        dre_note(f"({dre['tarifa_pagamento']['count']} lancamentos - DIFAL + non-orders)")
    if total_sub > 0:
        dre_row("2.6.x  Assinaturas SaaS", -total_sub)
        dre_note(f"({dre['subscription_saas']['count']} assinaturas)")
    if total_boleto > 0:
        dre_row("2.x.x  Boletos / Outras Despesas", -total_boleto)
        dre_note(f"({dre['bill_payment']['count']} boletos e outras despesas MP)")
    if total_cobranca > 0:
        dre_row("2.8.2  Cobrancas ML", -total_cobranca)
    if total_gap_exp > 0:
        dre_row("2.x.x  Outros Debitos (extrato)", -total_gap_exp)
        dre_note(f"({dre['extrato_gap_expense']['count']} linhas do extrato)")

    print("|" + " " * W + "|")
    print("|" + f"{'':5}{'─'*45}{'─'*20}" + "|")
    dre_row("TOTAL DESPESAS", -total_despesas, indent=5)
    print("|" + " " * W + "|")
    print("|" + "=" * W + "|")
    dre_row("RESULTADO DO PERIODO", resultado, indent=5)
    if total_receitas != 0:
        margem = resultado / total_receitas * 100
        dre_note(f"Margem sobre receita liquida: {margem:.1f}%", indent=5)
    print("|" + "=" * W + "|")
    print("|" + " " * W + "|")

    # ── MEMO ─────────────────────────────────────────────────────────────────
    print("|" + f"  {'MEMO: Extrato Bancario (total movimentacao jan)':<{W-2}}" + "|")
    print("|" + " " * W + "|")
    dre_row("Total extrato janeiro:", extrato_total_jan)
    dre_note(f"  Creditos: {fmt_brl(extrato_summary['credits'])}  Debitos: {fmt_brl(extrato_summary['debits'])}")
    dre_note(f"  Saldo inicial: {fmt_brl(extrato_summary['initial_balance'])}  "
             f"Saldo final: {fmt_brl(extrato_summary['final_balance'])}")
    print("|" + " " * W + "|")

    # ── EXCLUIDOS (JAN aprovados, FEV+ liberados) ─────────────────────────
    jan_feb = cross["jan_approved_feb_release"]
    if jan_feb:
        total_exc = sum(p["amount"] for p in jan_feb)
        net_exc   = sum(p["net"] for p in jan_feb)
        dre_note(f"[EXCLUIDOS] Aprovados em JAN, liberados em FEV+: {len(jan_feb)} payments")
        dre_note(f"  Receita bruta: {fmt_brl(total_exc)}  Net: {fmt_brl(net_exc)}")
        dre_note(f"  (contam no DRE COMPETENCIA de jan, NAO no DRE CAIXA de jan)")

    print("|" + " " * W + "|")
    print("+" + "=" * W + "+")
    print()


def print_summary_caixa(dre_data: dict, extrato_transactions: list[dict],
                         extrato_summary: dict, seller_slug: str) -> dict:
    dre   = dre_data["dre"]
    cross = dre_data["cross"]

    total_receita_bruta = dre["venda_ml"]["total"]
    total_devolucao     = dre["devolucao"]["total"]
    total_estorno_taxa  = dre["estorno_taxa"]["total"]
    total_estorno_frete = dre["estorno_frete"]["total"]
    total_cashback      = dre["cashback_ml"]["total"]
    total_deposito      = dre["deposito_avulso"]["total"]
    total_gap_income    = dre["extrato_gap_income"]["total"]
    total_receitas      = (total_receita_bruta - total_devolucao + total_estorno_taxa
                           + total_estorno_frete + total_cashback + total_deposito + total_gap_income)
    total_despesas      = (dre["comissao_ml"]["total"] + dre["frete_mercadoenvios"]["total"]
                           + dre["tarifa_pagamento"]["total"] + dre["subscription_saas"]["total"]
                           + dre["bill_payment"]["total"] + dre["collection_ml"]["total"]
                           + dre["extrato_gap_expense"]["total"])
    resultado = total_receitas - total_despesas

    print("\n" + "=" * 70)
    print(f"  RESUMO EXECUTIVO — DRE CAIXA {seller_slug.upper()} JANEIRO 2026")
    print("=" * 70)
    print()
    print(f"  Receita bruta (1.1.1):          {fmt_brl(total_receita_bruta)}")
    if total_devolucao > 0:
        print(f"  (-) Devolucoes (1.2.1):         {fmt_brl(-total_devolucao)}")
    if total_estorno_taxa > 0:
        print(f"  (+) Estornos de taxas (1.3.4):  {fmt_brl(total_estorno_taxa)}")
    if total_cashback > 0:
        print(f"  (+) Cashback ML (1.3.4):        {fmt_brl(total_cashback)}")
    if total_deposito > 0:
        print(f"  (+) Depositos avulsos:          {fmt_brl(total_deposito)}")
    if total_gap_income > 0:
        print(f"  (+) Outros creditos extrato:    {fmt_brl(total_gap_income)}")
    print(f"  ─────────────────────────────────────────────────────")
    print(f"  RECEITAS LIQUIDAS:              {fmt_brl(total_receitas)}")
    print()
    print(f"  (-) Comissoes ML (2.8.2):       {fmt_brl(-dre['comissao_ml']['total'])}")
    print(f"  (-) Frete seller (2.9.4):       {fmt_brl(-dre['frete_mercadoenvios']['total'])}")
    if dre["tarifa_pagamento"]["total"] > 0:
        print(f"  (-) DIFAL/Tarifas (2.2.7):      {fmt_brl(-dre['tarifa_pagamento']['total'])}")
    if dre["subscription_saas"]["total"] > 0:
        print(f"  (-) Assinaturas SaaS (2.6.x):   {fmt_brl(-dre['subscription_saas']['total'])}")
    if dre["bill_payment"]["total"] > 0:
        print(f"  (-) Boletos/Outras (2.x.x):     {fmt_brl(-dre['bill_payment']['total'])}")
    if dre["collection_ml"]["total"] > 0:
        print(f"  (-) Cobrancas ML (2.8.2):       {fmt_brl(-dre['collection_ml']['total'])}")
    if dre["extrato_gap_expense"]["total"] > 0:
        print(f"  (-) Outros debitos extrato:     {fmt_brl(-dre['extrato_gap_expense']['total'])}")
    print(f"  ─────────────────────────────────────────────────────")
    print(f"  TOTAL DESPESAS:                 {fmt_brl(-total_despesas)}")
    print()
    print(f"  ═════════════════════════════════════════════════════")
    print(f"  RESULTADO DO PERIODO:           {fmt_brl(resultado)}")
    if total_receitas != 0:
        margem = resultado / total_receitas * 100
        print(f"  Margem liquida:                 {margem:.1f}%")
    print(f"  ═════════════════════════════════════════════════════")
    print()
    print(f"  Composicao da Receita Bruta por mes de aprovacao:")
    dec_jan = cross["dec_approved_jan_release"]
    jan_jan = cross["jan_approved_jan_release"]
    jan_feb = cross["jan_approved_feb_release"]
    print(f"    DEZ aprovados, JAN liberados:  {len(dec_jan):>4} payments  "
          f"({fmt_brl(sum(p['amount'] for p in dec_jan))})")
    print(f"    JAN aprovados, JAN liberados:  {len(jan_jan):>4} payments  "
          f"({fmt_brl(sum(p['amount'] for p in jan_jan))})")
    print(f"  [Excluidos] JAN aprov, FEV lib:  {len(jan_feb):>4} payments  "
          f"({fmt_brl(sum(p['amount'] for p in jan_feb))})  ← nao entram no DRE caixa jan")
    print()
    print(f"  Extrato bancario janeiro:")
    jan_txs = [tx for tx in extrato_transactions if tx["date"][:7] == "2026-01"]
    print(f"    Total movimentacao:           {fmt_brl(sum(tx['amount'] for tx in jan_txs))}")
    print(f"    Saldo inicial:                {fmt_brl(extrato_summary['initial_balance'])}")
    print(f"    Saldo final:                  {fmt_brl(extrato_summary['final_balance'])}")
    print()
    print("=" * 70 + "\n")

    return {"resultado": resultado, "receitas": total_receitas, "despesas": total_despesas}


def print_breakdown(dre_data: dict, non_order_jan: list[dict], extrato_gaps: list[dict]) -> None:
    dre = dre_data["dre"]
    print("\n" + "=" * 70)
    print("  BREAKDOWN DETALHADO POR CATEGORIA")
    print("=" * 70)
    print()

    print("  RECEITAS:")
    print(f"  {'Categoria':<45} {'Qtd':>5}  {'Total (R$)':>15}")
    print(f"  {'-'*45} {'-'*5}  {'-'*15}")
    for key, label, positive in [
        ("venda_ml", "1.1.1 Receita Bruta ML", True),
        ("devolucao", "1.2.1 (-) Devolucoes", False),
        ("estorno_taxa", "1.3.4 (+) Estornos de Taxas", True),
        ("estorno_frete", "1.3.7 (+) Subsidio/Estorno Frete", True),
        ("cashback_ml", "1.3.4 (+) Cashback ML", True),
        ("deposito_avulso", "1.x.x (+) Depositos Avulsos", True),
        ("extrato_gap_income", "1.3.x (+) Outros Creditos", True),
    ]:
        d = dre.get(key, {"total": 0.0, "count": 0})
        if d["total"] > 0:
            sign = "" if positive else "-"
            print(f"  {label:<45} {d['count']:>5}  {sign}{d['total']:>14,.2f}")
    print()

    print("  DESPESAS:")
    print(f"  {'Categoria':<45} {'Qtd':>5}  {'Total (R$)':>15}")
    print(f"  {'-'*45} {'-'*5}  {'-'*15}")
    for key, label in [
        ("comissao_ml", "2.8.2 Comissoes Marketplace"),
        ("frete_mercadoenvios", "2.9.4 MercadoEnvios Frete"),
        ("tarifa_pagamento", "2.2.7 Tarifas/DIFAL"),
        ("subscription_saas", "2.6.x Assinaturas SaaS"),
        ("bill_payment", "2.x.x Boletos/Outras"),
        ("collection_ml", "2.8.2 Cobrancas ML"),
        ("extrato_gap_expense", "2.x.x Outros Debitos (extrato)"),
    ]:
        d = dre.get(key, {"total": 0.0, "count": 0})
        if d["total"] > 0:
            print(f"  {label:<45} {d['count']:>5}  {d['total']:>14,.2f}")
    print()

    non_order_types = defaultdict(lambda: {"count": 0, "total": 0.0})
    for sim in non_order_jan:
        if sim["action"] == "NON_ORDER" and (sim.get("competencia") or "")[:7] == "2026-01":
            k = f"{sim.get('expense_type','?')} ({sim.get('direction','?')})"
            non_order_types[k]["count"] += 1
            non_order_types[k]["total"] += abs(sim.get("amount", 0))
    if non_order_types:
        print("  NON-ORDERS CLASSIFICADOS (date_approved em jan/2026):")
        print(f"  {'Tipo (direcao)':<45} {'Qtd':>5}  {'Total (R$)':>15}")
        print(f"  {'-'*45} {'-'*5}  {'-'*15}")
        for k, v in sorted(non_order_types.items()):
            print(f"  {k:<45} {v['count']:>5}  {v['total']:>14,.2f}")
        print()


# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════

def run_seller_caixa(seller_slug: str) -> dict:
    extrato_name = SELLER_EXTRATO_MAP.get(seller_slug)
    if not extrato_name:
        print(f"ERRO: Seller '{seller_slug}' nao mapeado. Disponiveis: {', '.join(ALL_SELLERS)}")
        return {"error": "seller not found"}

    extrato_file = EXTRATOS_DIR / extrato_name
    cache_file   = CACHE_DIR / f"{seller_slug}_payments.json"

    print()
    print("=" * 70)
    print(f"  DRE CAIXA — {seller_slug.upper()} — JANEIRO 2026 (VENCIMENTO)")
    print(f"  Criterio: money_release_date em jan/2026 (regime de caixa)")
    print("=" * 70)

    if not cache_file.exists():
        print(f"\nERRO: Cache nao encontrado: {cache_file}")
        return {"error": "cache not found"}

    # ── Carrega cache ─────────────────────────────────────────────────────────
    with open(cache_file) as f:
        cache_data = json.load(f)
    payments_all = cache_data["payments"]
    print(f"\nCache: {len(payments_all)} payments")

    # ── Simula todos ──────────────────────────────────────────────────────────
    simulated_all = [simulate_payment(p) for p in payments_all]

    # Non-orders com date_approved em jan (liquidacao imediata no MP)
    non_order_jan = [s for s in simulated_all
                     if s["action"] == "NON_ORDER"
                     and (s.get("competencia") or "")[:7] == "2026-01"]

    # Payments com release em jan (para informacao)
    n_release_jan = sum(
        1 for s in simulated_all
        if s["action"] in ("APPROVED", "CHARGED_BACK_REIMBURSED")
        and s.get("money_release_date", "")[:7] == "2026-01"
    )
    n_release_fev = sum(
        1 for s in simulated_all
        if s["action"] in ("APPROVED", "CHARGED_BACK_REIMBURSED")
        and (s.get("money_release_date") or "")[:7] >= "2026-02"
    )
    print(f"Aprovados com release jan/2026:  {n_release_jan}")
    print(f"Aprovados com release fev+/2026: {n_release_fev} (excluidos do DRE caixa jan)")
    print(f"Non-orders com comp jan/2026:    {len(non_order_jan)}")

    # ── Extrato ───────────────────────────────────────────────────────────────
    print(f"\nLendo extrato: {extrato_name}")
    extrato_summary, extrato_transactions = parse_extrato(extrato_file)
    jan_txs = [tx for tx in extrato_transactions if tx["date"][:7] == "2026-01"]
    print(f"Linhas jan: {len(jan_txs)}  "
          f"Creditos: {fmt_brl(extrato_summary['credits'])}  "
          f"Debitos: {fmt_brl(extrato_summary['debits'])}")

    # ── Gaps do extrato ───────────────────────────────────────────────────────
    extrato_gaps = classify_extrato_gaps(extrato_transactions, simulated_all)
    jan_gaps = [g for g in extrato_gaps if g.get("date", "")[:7] == "2026-01"]
    print(f"Gaps extrato: {len(jan_gaps)} em jan")

    # ── Constroi DRE caixa ────────────────────────────────────────────────────
    print("\nConstruindo DRE por vencimento (money_release_date)...")
    dre_data = build_dre_caixa(simulated_all, non_order_jan, extrato_gaps)

    # ── Impressao ─────────────────────────────────────────────────────────────
    print_dre_caixa(dre_data, extrato_summary, extrato_transactions, simulated_all, seller_slug)
    print_breakdown(dre_data, non_order_jan, extrato_gaps)
    result = print_summary_caixa(dre_data, extrato_transactions, extrato_summary, seller_slug)

    return result


def main():
    parser = argparse.ArgumentParser(description="DRE Caixa — Janeiro 2026 (money_release_date)")
    parser.add_argument("--seller", type=str, default=None)
    parser.add_argument("--all",    action="store_true")
    args = parser.parse_args()

    sellers = ALL_SELLERS if args.all else [args.seller or "141air"]
    results = {}
    for slug in sellers:
        results[slug] = run_seller_caixa(slug)

    if len(sellers) > 1:
        print("\n" + "=" * 70)
        print("  SUMARIO FINAL DRE CAIXA — TODOS OS SELLERS — JANEIRO 2026")
        print("=" * 70)
        print()
        print(f"  {'Seller':<20} {'Receitas':>15} {'Despesas':>15} {'Resultado':>15} {'Margem':>8}")
        print(f"  {'-'*20} {'-'*15} {'-'*15} {'-'*15} {'-'*8}")
        for slug in sellers:
            r = results[slug]
            if "error" in r:
                print(f"  {slug:<20} {'ERRO':>15} {r['error']}")
            else:
                margem = r["resultado"] / r["receitas"] * 100 if r["receitas"] else 0
                print(f"  {slug:<20} {fmt_brl(r['receitas']):>15} "
                      f"{fmt_brl(-r['despesas']):>15} {fmt_brl(r['resultado']):>15} "
                      f"{margem:>7.1f}%")
        print("=" * 70 + "\n")


if __name__ == "__main__":
    main()
