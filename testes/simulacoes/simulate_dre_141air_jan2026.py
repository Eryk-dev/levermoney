#!/usr/bin/env python3
"""
DRE Simulacao — Janeiro 2026 (Competencia) — Agnostico
=======================================================
Demonstracao do Resultado do Exercicio usando regime de competencia:
  - Receita reconhecida em date_approved (BRT), NAO em money_release_date
  - Despesas (comissao, frete) reconhecidas na mesma competencia da receita
  - Devolucoes reconhecidas na competencia do estorno

DISTINCAO CRITICA:
  DRE = usa date_approved (competencia/accrual)
  Caixa = usa money_release_date (liberacao efetiva)

  Uma venda aprovada em dez/25 com liberacao em jan/26:
    -> aparece no DRE de DEZEMBRO (nao de janeiro)
    -> aparece no caixa de JANEIRO

NAO grava nada no Conta Azul. NAO altera Supabase.
Apenas leitura e calculo.

Uso:
    cd "lever money claude v3"
    python3 testes/simulate_dre_141air_jan2026.py [--seller SLUG]

    # Exemplos:
    python3 testes/simulate_dre_141air_jan2026.py --seller net-air
    python3 testes/simulate_dre_141air_jan2026.py --seller 141air
    python3 testes/simulate_dre_141air_jan2026.py --all
"""

import sys
import os
import json
import logging
import argparse
from pathlib import Path
from datetime import date, datetime, timedelta, timezone
from collections import defaultdict

# ── Configuracao do projeto ───────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.services.processor import _to_brt_date, _to_float, _compute_effective_net_amount
from app.services.expense_classifier import _classify, _extract_branch
from app.services.extrato_ingester import _classify_extrato_line, _normalize_text

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("simulate_dre")

# ── Constantes ────────────────────────────────────────────────────────────────
JAN_START = "2026-01-01"
JAN_END = "2026-01-31"
DEC_START = "2025-12-01"
DEC_END = "2025-12-31"

CACHE_DIR = PROJECT_ROOT / "testes" / "cache_jan2026"
EXTRATOS_DIR = PROJECT_ROOT / "testes" / "extratos"

# Mapeamento seller_slug → nome do arquivo de extrato
SELLER_EXTRATO_MAP = {
    "141air":          "extrato janeiro 141Air.csv",
    "net-air":         "extrato janeiro netair.csv",
    "netparts-sp":     "extrato janeiro netparts.csv",
    "easy-utilidades": "extrato janeiro Easyutilidades.csv",
}

ALL_SELLERS = list(SELLER_EXTRATO_MAP.keys())

BRT = timezone(timedelta(hours=-3))

JAN_DATES = [(date(2026, 1, 1) + timedelta(days=i)).isoformat() for i in range(31)]


def get_seller_paths(seller_slug: str) -> tuple[Path, Path]:
    """Retorna (extrato_file, cache_file) para o seller."""
    extrato_name = SELLER_EXTRATO_MAP.get(seller_slug)
    if not extrato_name:
        print(f"ERRO: Seller '{seller_slug}' nao tem extrato mapeado.")
        print(f"Sellers disponiveis: {', '.join(ALL_SELLERS)}")
        sys.exit(1)
    return EXTRATOS_DIR / extrato_name, CACHE_DIR / f"{seller_slug}_payments.json"

# Nomes de categorias CA para exibicao no DRE
CA_CATEGORY_NAMES = {
    "venda_ml":            "1.1.1  MercadoLibre (Receita)",
    "devolucao":           "1.2.1  Devolucoes e Cancelamentos",
    "estorno_taxa":        "1.3.4  Estornos de Taxas",
    "estorno_frete":       "1.3.7  Estorno de Frete",
    "comissao_ml":         "2.8.2  Comissoes Marketplace",
    "frete_mercadoenvios": "2.9.4  MercadoEnvios (Frete Seller)",
    "tarifa_pagamento":    "2.2.7  Tarifas / Impostos (DIFAL)",
    "subscription_saas":   "2.6.x  Assinaturas SaaS",
    "bill_payment":        "2.x.x  Boletos / Outras Despesas",
    "collection_ml":       "2.8.2  Cobrancas ML",
    "extrato_gap_expense": "2.x.x  Gaps Extrato (Despesas)",
    "extrato_gap_income":  "1.3.x  Gaps Extrato (Creditos)",
    "cashback_ml":         "1.3.4  Cashback / Ressarcimento ML",
    "deposito_avulso":     "1.x.x  Deposito / Aporte Avulso",
}


# ══════════════════════════════════════════════════════════════════════════════
# AUXILIARES — Formatacao monetaria
# ══════════════════════════════════════════════════════════════════════════════

def fmt_brl(value: float) -> str:
    """Formata valor no padrao brasileiro: R$ 1.234,56"""
    negative = value < 0
    abs_val = abs(value)
    formatted = f"{abs_val:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    prefix = "R$ -" if negative else "R$  "
    return f"{prefix}{formatted}"


def fmt_pct(num: float, denom: float) -> str:
    if denom == 0:
        return "N/D"
    return f"{num / denom * 100:.1f}%"


def parse_br_number(raw: str) -> float:
    if not raw or not raw.strip():
        return 0.0
    cleaned = raw.strip().replace(".", "").replace(",", ".")
    try:
        return float(cleaned)
    except ValueError:
        return 0.0


# ══════════════════════════════════════════════════════════════════════════════
# FASE 1 — Parse do extrato real
# ══════════════════════════════════════════════════════════════════════════════

def parse_extrato(csv_path: Path) -> tuple[dict, list[dict]]:
    """Faz parse do extrato CSV no formato semicolon-delimitado."""
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
            date_str = parts[0]
            balance_str = parts[-1]
            amount_str = parts[-2]
            ref_id = parts[-3]
            tx_type = ";".join(parts[1:-3])

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
# FASE 2 — Simulacao do processor (logica de competencia)
# ══════════════════════════════════════════════════════════════════════════════

def simulate_approved_calcs(payment: dict) -> dict:
    """
    Calcula os lancamentos CA para pagamento aprovado.
    Usa regime de competencia: data_competencia = _to_brt_date(date_approved).
    """
    amount = _to_float(payment.get("transaction_amount"))
    td = payment.get("transaction_details") or {}
    net = _to_float(td.get("net_received_amount"))
    effective_net = _compute_effective_net_amount(payment)

    date_approved_raw = payment.get("date_approved") or payment.get("date_created", "")
    competencia = _to_brt_date(date_approved_raw)
    money_release_date = (payment.get("money_release_date") or date_approved_raw)[:10]

    charges = payment.get("charges_details") or []
    shipping_collector = 0.0
    mp_fee = 0.0

    for charge in charges:
        accounts = charge.get("accounts") or {}
        if accounts.get("from") != "collector":
            continue
        charge_amount = _to_float((charge.get("amounts") or {}).get("original", 0))
        charge_type = charge.get("type")

        if charge_type == "shipping":
            shipping_collector += charge_amount
        elif charge_type == "fee":
            name = (charge.get("name") or "").strip().lower()
            if name == "financing_fee":
                continue
            mp_fee += charge_amount

    shipping_buyer = _to_float(payment.get("shipping_amount"))
    shipping_seller = round(max(0.0, shipping_collector - shipping_buyer), 2)
    mp_fee = round(mp_fee, 2)
    reconciled_net = round(amount - mp_fee - shipping_seller, 2)
    net_diff = round(net - reconciled_net, 2)

    # Subsidio ML (quando net calculado < net real)
    subsidy = round(net - reconciled_net, 2) if net_diff > 0 else 0.0

    return {
        "amount": amount,
        "net": net,
        "effective_net": effective_net,
        "comissao": mp_fee,
        "frete": shipping_seller,
        "subsidy": subsidy,
        "competencia": competencia,
        "money_release_date": money_release_date,
        "net_diff": net_diff,
        "reconciled_net": reconciled_net,
    }


def simulate_refunded_calcs(payment: dict) -> dict:
    """
    Calcula os lancamentos CA para pagamento devolvido.
    A competencia do ESTORNO e a data do refund (date_created do refund).
    A receita original foi reconhecida na competencia de date_approved.
    """
    amount = _to_float(payment.get("transaction_amount"))
    td = payment.get("transaction_details") or {}
    net = _to_float(td.get("net_received_amount"))
    refunds = payment.get("refunds") or []

    if refunds:
        total_refunded = sum(_to_float(r.get("amount")) for r in refunds)
        date_refunded = refunds[-1].get("date_created", "")[:10]
        if date_refunded:
            date_refunded = _to_brt_date(refunds[-1].get("date_created", ""))
    else:
        total_refunded = _to_float(payment.get("transaction_amount_refunded")) or amount
        raw_date = payment.get("date_last_updated") or payment.get("date_created", "")
        date_refunded = _to_brt_date(raw_date)

    estorno_receita = min(total_refunded, amount)
    total_fees = round(amount - net, 2) if net > 0 else 0
    approved = simulate_approved_calcs(payment)

    return {
        "amount": amount,
        "net": net,
        "effective_net": _compute_effective_net_amount(payment),
        "comissao": approved["comissao"],
        "frete": approved["frete"],
        "competencia_original": approved["competencia"],
        "competencia_estorno": date_refunded,
        "money_release_date": approved["money_release_date"],
        "estorno_receita": estorno_receita,
        "estorno_taxa": total_fees if estorno_receita >= amount else 0,
        "total_refunded_raw": total_refunded,
    }


def simulate_payment_for_dre(payment: dict) -> dict:
    """
    Dispatcher principal: simula o que o processor faria com cada payment.
    Retorna dict com todos os campos necessarios para o DRE.
    """
    pid = payment["id"]
    status = payment.get("status", "")
    status_detail = payment.get("status_detail", "")
    order_id = (payment.get("order") or {}).get("id")
    op_type = payment.get("operation_type", "")

    base = {
        "payment_id": pid,
        "order_id": order_id,
        "ml_status": status,
        "status_detail": status_detail,
        "operation_type": op_type,
        "transaction_amount": _to_float(payment.get("transaction_amount")),
        "money_release_date": (payment.get("money_release_date") or "")[:10],
        "date_approved_raw": payment.get("date_approved", ""),
    }

    # ── Sem order_id: classificar como non-order ──────────────────────────────
    if not order_id:
        exp_type, direction, category, auto, desc = _classify(payment)
        if direction == "skip":
            return {**base, "action": "SKIP", "skip_reason": f"non-order interno ({exp_type})"}
        date_approved_raw = payment.get("date_approved") or payment.get("date_created", "")
        amount = _to_float(payment.get("transaction_amount"))
        return {
            **base,
            "action": "NON_ORDER",
            "expense_type": exp_type,
            "direction": direction,
            "category": category,
            "auto_categorized": auto,
            "description": desc,
            "amount": amount,
            "net": _to_float((payment.get("transaction_details") or {}).get("net_received_amount")),
            "competencia": _to_brt_date(date_approved_raw),
            "date_created": _to_brt_date(payment.get("date_created", "")),
        }

    # ── Filtros de skip para orders ───────────────────────────────────────────
    if payment.get("description") == "marketplace_shipment":
        return {**base, "action": "SKIP", "skip_reason": "marketplace_shipment"}

    if (payment.get("collector") or {}).get("id") is not None:
        return {**base, "action": "SKIP", "skip_reason": "compra (collector_id)"}

    # ── Dispatch por status ───────────────────────────────────────────────────
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
# FASE 3 — Construcao do DRE por competencia
# ══════════════════════════════════════════════════════════════════════════════

def build_dre_from_simulated(
    simulated_jan: list[dict],           # payments com date_approved em jan
    simulated_dec_jan_cash: list[dict],  # payments com date_approved em dez, release em jan
    non_order_jan: list[dict],           # non-orders com date_approved em jan
    extrato_gaps: list[dict],            # linhas do extrato nao cobertas pela API
) -> dict:
    """
    Constroi o DRE de janeiro 2026 por competencia.

    Mapeamento correto de tipos do extrato para linhas DRE:
      dinheiro_retido      → SKIP (cash flow only; nets to zero com reembolso_reclamacoes)
      liberacao_cancelada  → SKIP (reversao interna, neutro)
      reembolso_disputa    → 1.3.4 Estornos de Taxas (dinheiro retido devolvido ao ganhar mediacao)
      reembolso_generico   → 1.3.4 Estornos de Taxas
      bonus_envio          → 1.3.7 Estorno de Frete
      entrada_dinheiro     → 1.4.2 Outras Receitas Eventuais
      deposito_avulso      → 1.4.2 Outras Receitas Eventuais
      debito_divida_disputa → 1.2.1 Devolucoes (SKIP se payment ja foi refunded pelo processor)
      debito_troca         → 1.2.1 Devolucoes
      difal                → 2.2.3 DIFAL (Diferencial de Aliquota)  ← CORRECAO: antes era 2.2.7
      faturas_ml           → 2.8.2 Comissoes Marketplace
      debito_envio_ml      → 2.9.4 MercadoEnvios
      subscription (non-order) → 2.14.12 Assinaturas
    """

    dre = {
        # RECEITAS (valores positivos)
        "venda_ml":             {"total": 0.0, "count": 0},
        "devolucao":            {"total": 0.0, "count": 0},   # 1.2.1 — negativo nas receitas
        "estorno_taxa":         {"total": 0.0, "count": 0},   # 1.3.4
        "estorno_frete":        {"total": 0.0, "count": 0},   # 1.3.7
        "cashback_ml":          {"total": 0.0, "count": 0},   # 1.3.4
        "outras_receitas":      {"total": 0.0, "count": 0},   # 1.4.2
        "deposito_avulso":      {"total": 0.0, "count": 0},   # 1.4.2
        "extrato_gap_income":   {"total": 0.0, "count": 0},   # outros creditos

        # DESPESAS (valores positivos, apresentados como negativos no DRE)
        "comissao_ml":          {"total": 0.0, "count": 0},   # 2.8.2
        "frete_mercadoenvios":  {"total": 0.0, "count": 0},   # 2.9.4
        "difal_icms":           {"total": 0.0, "count": 0},   # 2.2.3 DIFAL
        "subscription_saas":    {"total": 0.0, "count": 0},   # 2.14.12
        "bill_payment":         {"total": 0.0, "count": 0},   # 2.x.x boletos
        "collection_ml":        {"total": 0.0, "count": 0},   # 2.8.2 faturas/cobrancas
        "extrato_gap_expense":  {"total": 0.0, "count": 0},   # outros debitos
    }

    # Cross-month tracking
    cross_month = {
        "dec_approved_jan_release": [],   # DRE dez, caixa jan
        "jan_approved_feb_release": [],   # DRE jan, caixa fev
        "jan_approved_jan_release": [],   # DRE jan, caixa jan
    }

    # IDs de payments refunded em jan pelo processor (para evitar dupla contagem com extrato)
    refunded_payment_ids: set[str] = set()
    for sim in simulated_jan:
        if sim["action"] == "REFUNDED":
            refunded_payment_ids.add(str(sim["payment_id"]))

    # ── ORDERS APROVADOS em jan (DRE jan) ─────────────────────────────────────
    for sim in simulated_jan:
        if sim["action"] not in ("APPROVED", "CHARGED_BACK_REIMBURSED"):
            continue

        amount = sim.get("amount", 0.0)
        comissao = sim.get("comissao", 0.0)
        frete = sim.get("frete", 0.0)
        subsidy = sim.get("subsidy", 0.0)
        release = sim.get("money_release_date", "")

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

        # Cross-month classificacao
        if release and release[:7] == "2026-02":
            cross_month["jan_approved_feb_release"].append({
                "payment_id": sim["payment_id"],
                "amount": amount,
                "net": sim.get("net", 0),
                "competencia": sim.get("competencia"),
                "money_release_date": release,
            })
        elif release and release[:7] == "2026-01":
            cross_month["jan_approved_jan_release"].append({
                "payment_id": sim["payment_id"],
                "amount": amount,
                "net": sim.get("net", 0),
                "competencia": sim.get("competencia"),
                "money_release_date": release,
            })

    # ── DEVOLUCOES em jan (competencia do ESTORNO em jan) ────────────────────
    for sim in simulated_jan:
        if sim["action"] != "REFUNDED":
            continue

        amount = sim.get("amount", 0.0)
        comissao = sim.get("comissao", 0.0)
        frete = sim.get("frete", 0.0)
        estorno_receita = sim.get("estorno_receita", 0.0)
        estorno_taxa = sim.get("estorno_taxa", 0.0)
        competencia_original = sim.get("competencia_original", "")
        competencia_estorno = sim.get("competencia_estorno", "")

        # Receita original (lancada na competencia de aprovacao)
        if competencia_original and competencia_original[:7] == "2026-01":
            dre["venda_ml"]["total"] += amount
            dre["venda_ml"]["count"] += 1
            if comissao > 0:
                dre["comissao_ml"]["total"] += comissao
                dre["comissao_ml"]["count"] += 1
            if frete > 0:
                dre["frete_mercadoenvios"]["total"] += frete
                dre["frete_mercadoenvios"]["count"] += 1

        # Estorno da receita (lancado na competencia do estorno)
        if competencia_estorno and competencia_estorno[:7] == "2026-01":
            dre["devolucao"]["total"] += estorno_receita
            dre["devolucao"]["count"] += 1

            if estorno_taxa > 0:
                dre["estorno_taxa"]["total"] += estorno_taxa
                dre["estorno_taxa"]["count"] += 1

    # ── NON-ORDERS em jan (classificados pelo expense_classifier) ─────────────
    for sim in non_order_jan:
        if sim["action"] != "NON_ORDER":
            continue

        amount = abs(sim.get("amount", 0.0))
        direction = sim.get("direction", "")
        exp_type = sim.get("expense_type", "")
        competencia = sim.get("competencia", "")

        if not competencia or competencia[:7] != "2026-01":
            continue

        if direction == "income":
            if exp_type == "cashback":
                dre["cashback_ml"]["total"] += amount
                dre["cashback_ml"]["count"] += 1
            else:
                dre["extrato_gap_income"]["total"] += amount
                dre["extrato_gap_income"]["count"] += 1
        elif direction == "expense":
            if exp_type == "darf":
                # DARF via non-order → 2.2.3 DIFAL (ou imposto similar)
                dre["difal_icms"]["total"] += amount
                dre["difal_icms"]["count"] += 1
            elif exp_type == "subscription":
                dre["subscription_saas"]["total"] += amount
                dre["subscription_saas"]["count"] += 1
            elif exp_type == "bill_payment":
                dre["bill_payment"]["total"] += amount
                dre["bill_payment"]["count"] += 1
            elif exp_type == "collection":
                dre["collection_ml"]["total"] += amount
                dre["collection_ml"]["count"] += 1
            else:
                dre["bill_payment"]["total"] += amount
                dre["bill_payment"]["count"] += 1
        # direction == "transfer": nao afeta DRE (movimentacao de saldo)

    # ── GAPS DO EXTRATO — CATEGORIZACAO CORRIGIDA ─────────────────────────────
    # Nota: dinheiro_retido e liberacao_cancelada sao omitidos do DRE pois
    # representam apenas movimentacao de caixa (se anulam).
    # debito_divida_disputa so entra se o payment NAO foi ja processado como
    # refunded pelo processor (evita dupla contagem da linha 1.2.1).
    for gap in extrato_gaps:
        amount = abs(gap.get("amount", 0.0))
        direction = gap.get("direction")
        exp_type = gap.get("expense_type", "")
        gap_date = gap.get("date", "")
        ref_id = gap.get("reference_id", "")

        if not gap_date or gap_date[:7] != "2026-01":
            continue

        # Cash-flow-only: neutros no DRE (retido + reembolso se anulam)
        if exp_type in ("dinheiro_retido", "liberacao_cancelada"):
            continue

        if direction == "income":
            if exp_type in ("reembolso_disputa", "reembolso_generico"):
                # Dinheiro retido devolvido (ganhou mediacao) → 1.3.4 Estornos
                dre["estorno_taxa"]["total"] += amount
                dre["estorno_taxa"]["count"] += 1
            elif exp_type == "bonus_envio":
                # Bonus por envio rapido → 1.3.7 Estorno de Frete
                dre["estorno_frete"]["total"] += amount
                dre["estorno_frete"]["count"] += 1
            elif exp_type in ("entrada_dinheiro", "deposito_avulso"):
                # Credito avulso → 1.4.2 Outras Receitas
                dre["outras_receitas"]["total"] += amount
                dre["outras_receitas"]["count"] += 1
            else:
                dre["extrato_gap_income"]["total"] += amount
                dre["extrato_gap_income"]["count"] += 1

        elif direction == "expense":
            if exp_type == "difal":
                # DIFAL ICMS → 2.2.3 (corrigido de 2.2.7)
                dre["difal_icms"]["total"] += amount
                dre["difal_icms"]["count"] += 1
            elif exp_type in ("debito_divida_disputa", "debito_troca"):
                # Devolucao/Cancelamento → 1.2.1
                # POREM: skip se processor.py ja gerou estorno_receita (1.2.1) para este payment
                if ref_id in refunded_payment_ids:
                    # Ja contabilizado como estorno no processamento de orders
                    continue
                dre["devolucao"]["total"] += amount
                dre["devolucao"]["count"] += 1
            elif exp_type == "faturas_ml":
                # Fatura vencida ML → 2.8.2 Comissoes/Cobrancas
                dre["collection_ml"]["total"] += amount
                dre["collection_ml"]["count"] += 1
            elif exp_type == "debito_envio_ml":
                # Debito retroativo de envio → 2.9.4 MercadoEnvios
                dre["frete_mercadoenvios"]["total"] += amount
                dre["frete_mercadoenvios"]["count"] += 1
            else:
                dre["extrato_gap_expense"]["total"] += amount
                dre["extrato_gap_expense"]["count"] += 1

    # ── ORDERS de DEZEMBRO com liberacao em JANEIRO (DRE dez, caixa jan) ─────
    for sim in simulated_dec_jan_cash:
        if sim["action"] not in ("APPROVED", "CHARGED_BACK_REIMBURSED"):
            continue
        cross_month["dec_approved_jan_release"].append({
            "payment_id": sim["payment_id"],
            "amount": sim.get("amount", 0),
            "net": sim.get("net", 0),
            "competencia": sim.get("competencia"),
            "money_release_date": sim.get("money_release_date"),
        })

    return {"dre": dre, "cross_month": cross_month, "refunded_ids": refunded_payment_ids}


# ══════════════════════════════════════════════════════════════════════════════
# FASE 4 — Analise do Extrato para Caixa
# ══════════════════════════════════════════════════════════════════════════════

def classify_extrato_category(tx_type: str) -> str:
    """Categoriza linha do extrato para exibicao."""
    t = _normalize_text(tx_type)
    if "liberacao de dinheiro cancelada" in t:
        return "liberacao_cancelada"
    if "liberacao de dinheiro" in t:
        return "liberacao"
    if "reembolso" in t:
        return "reembolso"
    if "dinheiro retido" in t:
        return "dinheiro_retido"
    if "debito por divida" in t:
        return "debito_divida"
    if "transferencia" in t:
        return "transferencia"
    if "pagamento de conta" in t:
        return "pagamento_conta"
    if "pagamento" in t:
        return "pagamento_qr_ou_subs"
    if "bonus" in t or "bônus" in t:
        return "bonus"
    if "dinheiro recebido" in t:
        return "dinheiro_recebido"
    if "entrada de dinheiro" in t:
        return "entrada_dinheiro"
    return "outro"


def compute_caixa_from_extrato(
    transactions: list[dict],
    simulated_all: list[dict],
    extrato_gaps: list[dict],
) -> dict:
    """
    Computa o caixa de janeiro a partir do extrato real.
    O caixa = soma de TODAS as linhas do extrato em janeiro.
    Diferente do DRE que usa competencia.
    """
    jan_txs = [tx for tx in transactions if tx["date"][:7] == "2026-01"]

    total_extrato = sum(tx["amount"] for tx in jan_txs)
    total_credits = sum(tx["amount"] for tx in jan_txs if tx["amount"] > 0)
    total_debits = sum(tx["amount"] for tx in jan_txs if tx["amount"] < 0)

    # Soma das liberacoes (baixas de payments)
    sim_by_id = {str(s["payment_id"]): s for s in simulated_all}
    total_liberacoes = 0.0
    liberacao_count = 0
    for tx in jan_txs:
        cat = classify_extrato_category(tx["type"])
        if cat == "liberacao":
            sim = sim_by_id.get(tx["reference_id"])
            if sim and sim["action"] in ("APPROVED", "CHARGED_BACK_REIMBURSED"):
                total_liberacoes += tx["amount"]
                liberacao_count += 1

    # Soma dos gaps do extrato (linhas nao cobertas pela API)
    total_gaps = sum(gap.get("amount", 0) for gap in extrato_gaps
                     if gap.get("date", "")[:7] == "2026-01")
    gap_count = len([g for g in extrato_gaps if g.get("date", "")[:7] == "2026-01"])

    return {
        "total_extrato": round(total_extrato, 2),
        "total_credits": round(total_credits, 2),
        "total_debits": round(total_debits, 2),
        "total_liberacoes": round(total_liberacoes, 2),
        "liberacao_count": liberacao_count,
        "total_gaps": round(total_gaps, 2),
        "gap_count": gap_count,
        "total_jan_lines": len(jan_txs),
    }


# ══════════════════════════════════════════════════════════════════════════════
# FASE 5 — Classificacao dos gaps do extrato
# ══════════════════════════════════════════════════════════════════════════════

def classify_extrato_gaps(
    transactions: list[dict],
    simulated_all: list[dict],
) -> list[dict]:
    """
    Identifica e classifica linhas do extrato nao cobertas pela API.
    Retorna lista de gap lines com tipo e direcao.
    """
    sim_by_id = {str(s["payment_id"]): s for s in simulated_all}
    gaps = []

    for tx in transactions:
        ref_id = tx["reference_id"]
        sim = sim_by_id.get(ref_id)

        # Se casou com payment API e nao e skip: coberto
        if sim and sim["action"] != "SKIP":
            continue

        # Classificar via regras do extrato_ingester
        expense_type, direction, ca_cat_uuid = _classify_extrato_line(tx["type"])

        # None, None, None = skip interno (liberacao, transferencia, pagamento_conta)
        if expense_type is None and direction is None:
            continue

        gaps.append({
            **tx,
            "expense_type": expense_type,
            "direction": direction,
            "ca_category": ca_cat_uuid,
        })

    return gaps


# ══════════════════════════════════════════════════════════════════════════════
# IMPRESSAO DO DRE
# ══════════════════════════════════════════════════════════════════════════════

def print_dre_report(
    dre_data: dict,
    extrato_summary: dict,
    extrato_transactions: list[dict],
    caixa_data: dict,
    simulated_jan: list[dict],
    simulated_dec_jan_cash: list[dict],
    non_order_jan: list[dict],
    extrato_gaps: list[dict],
    seller_slug: str = "",
) -> None:
    """Imprime o DRE formatado."""
    dre = dre_data["dre"]
    cross_month = dre_data["cross_month"]
    seller_upper = seller_slug.upper()

    # ── Calcula totais ────────────────────────────────────────────────────────
    total_receita_bruta = dre["venda_ml"]["total"]
    total_devolucao = dre["devolucao"]["total"]
    total_estorno_taxa = dre["estorno_taxa"]["total"]
    total_estorno_frete = dre["estorno_frete"]["total"]
    total_cashback = dre["cashback_ml"]["total"]
    total_outras = dre["outras_receitas"]["total"] + dre["deposito_avulso"]["total"]
    total_gap_income = dre["extrato_gap_income"]["total"]

    total_receitas_liquidas = (
        total_receita_bruta
        - total_devolucao
        + total_estorno_taxa
        + total_estorno_frete
        + total_cashback
        + total_outras
        + total_gap_income
    )

    total_comissao = dre["comissao_ml"]["total"]
    total_frete = dre["frete_mercadoenvios"]["total"]
    total_difal = dre["difal_icms"]["total"]
    total_sub = dre["subscription_saas"]["total"]
    total_boleto = dre["bill_payment"]["total"]
    total_cobranca = dre["collection_ml"]["total"]
    total_gap_expense = dre["extrato_gap_expense"]["total"]

    total_despesas = (
        total_comissao
        + total_frete
        + total_difal
        + total_sub
        + total_boleto
        + total_cobranca
        + total_gap_expense
    )

    resultado = total_receitas_liquidas - total_despesas

    # ── Calcula caixa (aprovados liberados em jan) ────────────────────────────
    approved_sims = [s for s in simulated_jan if s["action"] in ("APPROVED", "CHARGED_BACK_REIMBURSED")]
    total_net_aprovados_jan = sum(s.get("effective_net", s.get("net", 0)) for s in approved_sims
                                  if s.get("money_release_date", "")[:7] == "2026-01")

    dec_net_jan_cash = sum(s.get("effective_net", s.get("net", 0))
                           for s in cross_month["dec_approved_jan_release"])

    # ── IMPRIMIR DRE ─────────────────────────────────────────────────────────
    W = 70
    line = "=" * W

    dre_title = f"DRE - {seller_upper} - JANEIRO 2026 (COMPETENCIA)"
    print()
    print("+" + line + "+")
    print("|" + f"{dre_title:^{W}}" + "|")
    print("+" + line + "+")
    print("|" + " " * W + "|")

    def dre_row(label: str, value: float, indent: int = 5) -> None:
        prefix = " " * indent
        label_width = W - indent - 20
        val_str = fmt_brl(value)
        print("|" + f"{prefix}{label:<{label_width}}{val_str:>20}" + "|")

    def dre_note(text: str, indent: int = 8) -> None:
        prefix = " " * indent
        print("|" + f"{prefix}{text:<{W - indent}}" + "|")

    print("|" + f"  {'1. RECEITAS':<{W-2}}" + "|")
    print("|" + " " * W + "|")

    dre_row("1.1.1  Receita Bruta (MercadoLibre)",   total_receita_bruta)
    dre_note(f"({dre['venda_ml']['count']} vendas aprovadas em jan/2026 por data_approved)")
    if total_devolucao > 0:
        dre_row("1.2.1  (-) Devolucoes e Cancelamentos", -total_devolucao)
        dre_note(f"({dre['devolucao']['count']} devolucoes com estorno em jan/2026)")
    if total_estorno_taxa > 0:
        dre_row("1.3.4  (+) Estornos de Taxas",         total_estorno_taxa)
        dre_note(f"({dre['estorno_taxa']['count']} estornos de taxa - devolucoes totais)")
    if total_estorno_frete > 0:
        dre_row("1.3.7  (+) Estorno de Frete / Subsidio ML", total_estorno_frete)
    if total_cashback > 0:
        dre_row("1.3.4  (+) Cashback / Ressarcimento ML", total_cashback)
        dre_note(f"({dre['cashback_ml']['count']} cashbacks)")
    if total_outras > 0:
        n_outras = dre["outras_receitas"]["count"] + dre["deposito_avulso"]["count"]
        dre_row("1.4.2  (+) Outras Receitas Eventuais",   total_outras)
        dre_note(f"({n_outras} entradas: depositos avulsos, creditos ML)")
    if total_gap_income > 0:
        dre_row("1.3.x  (+) Outros Creditos (extrato)",  total_gap_income)
        dre_note(f"({dre['extrato_gap_income']['count']} creditos do extrato)")

    print("|" + " " * W + "|")
    print("|" + f"{'':5}{'─' * 45}{'─'*20}" + "|")
    dre_row("TOTAL RECEITAS LIQUIDAS",               total_receitas_liquidas, indent=5)
    print("|" + " " * W + "|")
    print("|" + "-" * W + "|")
    print("|" + " " * W + "|")

    print("|" + f"  {'2. DESPESAS':<{W-2}}" + "|")
    print("|" + " " * W + "|")

    dre_row("2.8.2  Comissoes Marketplace",          -total_comissao)
    dre_note(f"({dre['comissao_ml']['count']} lancamentos de comissao ML)")
    dre_row("2.9.4  MercadoEnvios (Frete Seller)",   -total_frete)
    dre_note(f"({dre['frete_mercadoenvios']['count']} lancamentos de frete + debito_envio_ml)")
    if total_difal > 0:
        dre_row("2.2.3  DIFAL (Diferencial de Aliquota)", -total_difal)
        dre_note(f"({dre['difal_icms']['count']} lancamentos DIFAL ICMS)")
    if total_sub > 0:
        dre_row("2.14.12 Assinaturas (SaaS)",            -total_sub)
        dre_note(f"({dre['subscription_saas']['count']} assinaturas - Supabase, Claude.ai, Notion)")
    if total_boleto > 0:
        dre_row("2.x.x  Boletos / Outras Despesas",     -total_boleto)
        dre_note(f"({dre['bill_payment']['count']} boletos e outras despesas MP)")
    if total_cobranca > 0:
        dre_row("2.8.2  Faturas/Cobrancas ML",           -total_cobranca)
        dre_note(f"({dre['collection_ml']['count']} faturas vencidas ML + cobrancas)")
    if total_gap_expense > 0:
        dre_row("2.x.x  Outros Debitos (extrato)",      -total_gap_expense)
        dre_note(f"({dre['extrato_gap_expense']['count']} outros debitos nao classificados)")

    print("|" + " " * W + "|")
    print("|" + f"{'':5}{'─' * 45}{'─'*20}" + "|")
    dre_row("TOTAL DESPESAS",                         -total_despesas, indent=5)
    print("|" + " " * W + "|")
    print("|" + "=" * W + "|")
    dre_row("RESULTADO DO PERIODO",                   resultado, indent=5)
    if total_receitas_liquidas != 0:
        margem = resultado / total_receitas_liquidas * 100
        dre_note(f"Margem sobre receita liquida: {margem:.1f}%", indent=5)
    print("|" + "=" * W + "|")

    print("|" + " " * W + "|")
    print("|" + f"  {'MEMO: Fluxo de Caixa (money_release_date em Janeiro)':<{W-2}}" + "|")
    print("|" + " " * W + "|")

    # Caixa
    extrato_total_jan = sum(tx["amount"] for tx in extrato_transactions
                            if tx["date"][:7] == "2026-01")

    dre_row("Net vendas jan aprovadas, release=jan:", total_net_aprovados_jan)
    dre_row("+ Net vendas dez aprovadas, release=jan:", dec_net_jan_cash)
    caixa_total = total_net_aprovados_jan + dec_net_jan_cash
    dre_row("= Total net caixa vendas (API):",         caixa_total)
    print("|" + " " * W + "|")
    dre_row("Extrato real (total movimentacao jan):",  extrato_total_jan)
    dre_note(f"  Creditos: {fmt_brl(extrato_summary['credits'])}  "
             f"Debitos: {fmt_brl(extrato_summary['debits'])}")
    dre_note("  Nota: Extrato inclui transferencias/PIX que nao sao DRE.")
    dre_note("  DRE (competencia) e Caixa diferem pelo cross-month (veja secao abaixo).")
    print("|" + " " * W + "|")
    print("+" + line + "+")
    print()


# ══════════════════════════════════════════════════════════════════════════════
# SECAO: Breakdown detalhado por categoria
# ══════════════════════════════════════════════════════════════════════════════

def print_category_breakdown(
    simulated_jan: list[dict],
    non_order_jan: list[dict],
    extrato_gaps: list[dict],
    dre_data: dict,
) -> None:
    print("\n" + "=" * 70)
    print("  BREAKDOWN DETALHADO POR CATEGORIA")
    print("=" * 70)
    print()

    dre = dre_data["dre"]

    # Receitas
    print("  RECEITAS:")
    print(f"  {'Categoria':<45} {'Qtd':>5}  {'Total (R$)':>15}")
    print(f"  {'-'*45} {'-'*5}  {'-'*15}")
    receita_cats = [
        ("venda_ml",           "1.1.1 Receita Bruta ML",           True),
        ("devolucao",          "1.2.1 (-) Devolucoes",             False),
        ("estorno_taxa",       "1.3.4 (+) Estornos de Taxas",      True),
        ("estorno_frete",      "1.3.7 (+) Estorno de Frete",       True),
        ("cashback_ml",        "1.3.4 (+) Cashback ML",            True),
        ("outras_receitas",    "1.4.2 (+) Outras Receitas",        True),
        ("deposito_avulso",    "1.4.2 (+) Depositos Avulsos",      True),
        ("extrato_gap_income", "1.3.x (+) Outros Creditos",        True),
    ]
    for key, label, positive in receita_cats:
        data = dre.get(key, {"total": 0.0, "count": 0})
        if data["total"] > 0:
            sign = "" if positive else "-"
            print(f"  {label:<45} {data['count']:>5}  {sign}{data['total']:>14,.2f}")

    print()

    # Despesas
    print("  DESPESAS:")
    print(f"  {'Categoria':<45} {'Qtd':>5}  {'Total (R$)':>15}")
    print(f"  {'-'*45} {'-'*5}  {'-'*15}")
    despesa_cats = [
        ("comissao_ml",        "2.8.2 Comissoes Marketplace"),
        ("frete_mercadoenvios","2.9.4 MercadoEnvios Frete"),
        ("difal_icms",         "2.2.3 DIFAL (Diferencial de Aliquota)"),
        ("subscription_saas",  "2.14.12 Assinaturas SaaS"),
        ("bill_payment",       "2.x.x Boletos/Outras"),
        ("collection_ml",      "2.8.2 Faturas/Cobrancas ML"),
        ("extrato_gap_expense", "2.x.x Outros Debitos (extrato)"),
    ]
    for key, label in despesa_cats:
        data = dre.get(key, {"total": 0.0, "count": 0})
        if data["total"] > 0:
            print(f"  {label:<45} {data['count']:>5}  {data['total']:>14,.2f}")

    print()

    # Non-orders detalhamento
    non_order_types = defaultdict(lambda: {"count": 0, "total": 0.0})
    for sim in non_order_jan:
        if sim["action"] == "NON_ORDER" and sim.get("competencia", "")[:7] == "2026-01":
            exp_type = sim.get("expense_type", "?")
            direction = sim.get("direction", "?")
            non_order_types[f"{exp_type} ({direction})"]["count"] += 1
            non_order_types[f"{exp_type} ({direction})"]["total"] += abs(sim.get("amount", 0))

    if non_order_types:
        print("  NON-ORDERS CLASSIFICADOS (date_approved em jan/2026):")
        print(f"  {'Tipo (direcao)':<45} {'Qtd':>5}  {'Total (R$)':>15}")
        print(f"  {'-'*45} {'-'*5}  {'-'*15}")
        for key, v in sorted(non_order_types.items()):
            print(f"  {key:<45} {v['count']:>5}  {v['total']:>14,.2f}")
        print()

    # Gaps do extrato
    gap_types = defaultdict(lambda: {"count": 0, "total": 0.0})
    for gap in extrato_gaps:
        if gap.get("date", "")[:7] == "2026-01":
            exp_type = gap.get("expense_type", "?")
            direction = gap.get("direction", "?")
            gap_types[f"{exp_type} ({direction})"]["count"] += 1
            gap_types[f"{exp_type} ({direction})"]["total"] += abs(gap.get("amount", 0))

    if gap_types:
        print("  GAPS DO EXTRATO CLASSIFICADOS (jan/2026):")
        print(f"  {'Tipo (direcao)':<45} {'Qtd':>5}  {'Total (R$)':>15}")
        print(f"  {'-'*45} {'-'*5}  {'-'*15}")
        for key, v in sorted(gap_types.items()):
            print(f"  {key:<45} {v['count']:>5}  {v['total']:>14,.2f}")
        print()


# ══════════════════════════════════════════════════════════════════════════════
# SECAO: DFC — Demonstrativo de Fluxo de Caixa
# ══════════════════════════════════════════════════════════════════════════════

def print_dfc_report(
    extrato_transactions: list[dict],
    extrato_summary: dict,
    seller_slug: str = "",
) -> None:
    """
    Imprime o DFC (Demonstrativo de Fluxo de Caixa) de janeiro 2026.

    O DFC mostra a MOVIMENTACAO REAL do caixa do dia, extraida diretamente
    do account_statement (extrato ML/MP). Diferente do DRE (competencia),
    o DFC registra quando o dinheiro efetivamente entrou ou saiu.

    Categorias de fluxo:
      OPERACIONAL:
        + Liberacoes de vendas (pagamentos aprovados liberados)
        + Reembolsos recebidos (disputas ganhas, tarifas)
        + Bonus por envio
        + Entrada de dinheiro / Deposito avulso
        - DIFAL, faturas vencidas, debito envio
        - Dinheiro retido (garantia disputa — saida temporaria)
        - Debito por divida de disputa / troca
        - Liberacao cancelada
      FINANCEIRO / TRANSFERENCIAS:
        - PIX enviados, transferencias enviadas
        + PIX recebidos, transferencias recebidas
      NAO IDENTIFICADO:
        Linhas sem match nas regras (tipo=other)

    Validacao: saldo_inicial + DFC_total = saldo_final
    """
    jan_txs = [tx for tx in extrato_transactions if tx["date"][:7] == "2026-01"]

    # Classifica cada linha do extrato
    dfc = {
        "liberacao_vendas":    {"total": 0.0, "count": 0},   # entradas: liberacoes approved
        "reembolso":           {"total": 0.0, "count": 0},   # entradas: reembolsos
        "bonus_envio":         {"total": 0.0, "count": 0},   # entradas: bonus
        "deposito_avulso":     {"total": 0.0, "count": 0},   # entradas: depositos
        "entrada_outros":      {"total": 0.0, "count": 0},   # entradas: outros creditos
        "difal":               {"total": 0.0, "count": 0},   # saidas operacional
        "faturas_ml":          {"total": 0.0, "count": 0},   # saidas operacional
        "debito_envio_ml":     {"total": 0.0, "count": 0},   # saidas operacional
        "dinheiro_retido":     {"total": 0.0, "count": 0},   # saidas temporarias
        "liberacao_cancelada": {"total": 0.0, "count": 0},   # saidas (cancelamento)
        "debito_disputa":      {"total": 0.0, "count": 0},   # saidas: disputas perdidas
        "pagamento_conta":     {"total": 0.0, "count": 0},   # saidas: boletos pagos
        "subscription_mp":     {"total": 0.0, "count": 0},   # saidas: assinaturas MP
        "pix_saida":           {"total": 0.0, "count": 0},   # transferencias saida
        "pix_entrada":         {"total": 0.0, "count": 0},   # transferencias entrada
        "transferencia_saldo": {"total": 0.0, "count": 0},   # transferencias internas
        "nao_identificado":    {"total": 0.0, "count": 0},   # outros
    }

    for tx in jan_txs:
        amount = tx["amount"]
        t = _normalize_text(tx["type"])

        if "liberacao de dinheiro cancelada" in t:
            dfc["liberacao_cancelada"]["total"] += amount
            dfc["liberacao_cancelada"]["count"] += 1
        elif "liberacao de dinheiro" in t:
            dfc["liberacao_vendas"]["total"] += amount
            dfc["liberacao_vendas"]["count"] += 1
        elif "reembolso" in t or "bonus por envio" in t or "bônus por envio" in t:
            if "bonus" in t or "bônus" in t:
                dfc["bonus_envio"]["total"] += amount
                dfc["bonus_envio"]["count"] += 1
            else:
                dfc["reembolso"]["total"] += amount
                dfc["reembolso"]["count"] += 1
        elif "dinheiro retido" in t:
            dfc["dinheiro_retido"]["total"] += amount
            dfc["dinheiro_retido"]["count"] += 1
        elif "diferenca da aliquota" in t or "difal" in t:
            dfc["difal"]["total"] += amount
            dfc["difal"]["count"] += 1
        elif "faturas vencidas" in t:
            dfc["faturas_ml"]["total"] += amount
            dfc["faturas_ml"]["count"] += 1
        elif "envio do mercado livre" in t:
            dfc["debito_envio_ml"]["total"] += amount
            dfc["debito_envio_ml"]["count"] += 1
        elif "reclamacoes no mercado livre" in t or "reclamações no mercado livre" in t:
            dfc["debito_disputa"]["total"] += amount
            dfc["debito_disputa"]["count"] += 1
        elif "troca de produto" in t:
            dfc["debito_disputa"]["total"] += amount
            dfc["debito_disputa"]["count"] += 1
        elif "dinheiro recebido" in t or "entrada de dinheiro" in t:
            dfc["deposito_avulso"]["total"] += amount
            dfc["deposito_avulso"]["count"] += 1
        elif "transferencia recebida" in t or "transferência recebida" in t:
            dfc["pix_entrada"]["total"] += amount
            dfc["pix_entrada"]["count"] += 1
        elif ("transferencia pix" in t or "pix enviado" in t
              or "transferencia enviada" in t or "transferência enviada" in t):
            dfc["pix_saida"]["total"] += amount
            dfc["pix_saida"]["count"] += 1
        elif "transferencia de saldo" in t or "transferência de saldo" in t:
            dfc["transferencia_saldo"]["total"] += amount
            dfc["transferencia_saldo"]["count"] += 1
        elif "pagamento de conta" in t:
            dfc["pagamento_conta"]["total"] += amount
            dfc["pagamento_conta"]["count"] += 1
        elif "pagamento cartao" in t or "pagamento cartão" in t:
            dfc["pagamento_conta"]["total"] += amount
            dfc["pagamento_conta"]["count"] += 1
        elif "compra mercado libre" in t or "compra de " in t:
            dfc["pagamento_conta"]["total"] += amount
            dfc["pagamento_conta"]["count"] += 1
        elif "pagamento" in t:
            # Pagamentos de assinaturas SaaS via MP (Supabase, Claude.ai, Notion)
            dfc["subscription_mp"]["total"] += amount
            dfc["subscription_mp"]["count"] += 1
        else:
            dfc["nao_identificado"]["total"] += amount
            dfc["nao_identificado"]["count"] += 1

    # Calcula grupos
    total_entradas_op = (dfc["liberacao_vendas"]["total"] + dfc["reembolso"]["total"]
                         + dfc["bonus_envio"]["total"] + dfc["deposito_avulso"]["total"]
                         + dfc["entrada_outros"]["total"])
    total_saidas_op = (dfc["difal"]["total"] + dfc["faturas_ml"]["total"]
                       + dfc["debito_envio_ml"]["total"] + dfc["dinheiro_retido"]["total"]
                       + dfc["liberacao_cancelada"]["total"] + dfc["debito_disputa"]["total"]
                       + dfc["pagamento_conta"]["total"] + dfc["subscription_mp"]["total"])
    total_transf = (dfc["pix_saida"]["total"] + dfc["pix_entrada"]["total"]
                    + dfc["transferencia_saldo"]["total"])
    total_nao_id = dfc["nao_identificado"]["total"]
    total_dfc = total_entradas_op + total_saidas_op + total_transf + total_nao_id

    variacao = extrato_summary["final_balance"] - extrato_summary["initial_balance"]

    print("\n" + "=" * 70)
    print(f"  DFC — {seller_slug.upper()} — DEMONSTRATIVO DE FLUXO DE CAIXA — JAN/2026")
    print("=" * 70)
    print()
    print(f"  Saldo inicial (31/12/2025):     {fmt_brl(extrato_summary['initial_balance'])}")
    print()

    def dfc_row(label: str, value: float, indent: int = 4) -> None:
        s = "" if value >= 0 else ""  # sign is implicit in fmt_brl
        print(f"  {' '*indent}{label:<43} {fmt_brl(value):>15}")

    def dfc_sub(label: str, value: float, count: int) -> None:
        if abs(value) >= 0.01:
            print(f"    {'  ' + label:<45} {fmt_brl(value):>15}  ({count}x)")

    # ATIVIDADES OPERACIONAIS
    print(f"  {'ATIVIDADES OPERACIONAIS'}")
    print(f"    {'Entradas:':}")
    dfc_sub("(+) Liberacoes de vendas aprovadas", dfc["liberacao_vendas"]["total"], dfc["liberacao_vendas"]["count"])
    dfc_sub("(+) Reembolsos (disputas, tarifas)", dfc["reembolso"]["total"], dfc["reembolso"]["count"])
    dfc_sub("(+) Bonus por envio rapido", dfc["bonus_envio"]["total"], dfc["bonus_envio"]["count"])
    dfc_sub("(+) Depositos avulsos / Entrada", dfc["deposito_avulso"]["total"], dfc["deposito_avulso"]["count"])
    dfc_row("= Total entradas operacionais:", total_entradas_op)
    print()
    print(f"    {'Saidas:':}")
    dfc_sub("(-) Dinheiro retido (garantia disputa)", dfc["dinheiro_retido"]["total"], dfc["dinheiro_retido"]["count"])
    dfc_sub("(-) Debito por disputa / troca", dfc["debito_disputa"]["total"], dfc["debito_disputa"]["count"])
    dfc_sub("(-) DIFAL ICMS", dfc["difal"]["total"], dfc["difal"]["count"])
    dfc_sub("(-) Faturas vencidas ML", dfc["faturas_ml"]["total"], dfc["faturas_ml"]["count"])
    dfc_sub("(-) Debito envio ML retroativo", dfc["debito_envio_ml"]["total"], dfc["debito_envio_ml"]["count"])
    dfc_sub("(-) Liberacao cancelada", dfc["liberacao_cancelada"]["total"], dfc["liberacao_cancelada"]["count"])
    dfc_sub("(-) Pagamentos de conta (boletos)", dfc["pagamento_conta"]["total"], dfc["pagamento_conta"]["count"])
    dfc_sub("(-) Assinaturas SaaS via MP", dfc["subscription_mp"]["total"], dfc["subscription_mp"]["count"])
    dfc_row("= Total saidas operacionais:", total_saidas_op)
    print()
    dfc_row("FLUXO OPERACIONAL LIQUIDO:", total_entradas_op + total_saidas_op)
    print()

    # TRANSFERENCIAS E FINANCIAMENTOS
    if abs(total_transf) >= 0.01:
        print(f"  {'TRANSFERENCIAS / ATIVIDADES FINANCEIRAS'}")
        dfc_sub("PIX / Transferencias enviadas", dfc["pix_saida"]["total"], dfc["pix_saida"]["count"])
        dfc_sub("PIX / Transferencias recebidas", dfc["pix_entrada"]["total"], dfc["pix_entrada"]["count"])
        dfc_sub("Transferencias de saldo internas", dfc["transferencia_saldo"]["total"], dfc["transferencia_saldo"]["count"])
        dfc_row("= Total transferencias:", total_transf)
        print()

    if abs(total_nao_id) >= 0.01:
        print(f"  {'NAO IDENTIFICADO'}")
        dfc_sub("Linhas sem classificacao", dfc["nao_identificado"]["total"], dfc["nao_identificado"]["count"])
        print()

    print(f"  {'─'*65}")
    print(f"  {'VARIACAO LIQUIDA DE CAIXA (DFC total):':<43} {fmt_brl(total_dfc):>15}")
    print(f"  {'Variacao extrato (final - inicial):':<43} {fmt_brl(variacao):>15}")
    diff = round(total_dfc - variacao, 2)
    status = "OK ✓" if abs(diff) < 0.02 else f"DIVERGENCIA: {fmt_brl(diff)}"
    print(f"  {'Diferenca DFC vs extrato:':<43} {fmt_brl(diff):>15}  {status}")
    print()
    print(f"  Saldo final (31/01/2026):       {fmt_brl(extrato_summary['final_balance'])}")
    print()
    print(f"  Nota: 'dinheiro_retido' e 'reembolso_reclamacoes' se anulam no DFC total.")
    print(f"  O DRE exclui essas linhas pois sao neutras economicamente.")
    print("=" * 70)
    print()


# ══════════════════════════════════════════════════════════════════════════════
# SECAO: Analise cross-month
# ══════════════════════════════════════════════════════════════════════════════

def print_cross_month_analysis(
    dre_data: dict,
    simulated_jan: list[dict],
    simulated_dec: list[dict],
) -> None:
    print("\n" + "=" * 70)
    print("  ANALISE CROSS-MONTH: Competencia vs Caixa")
    print("=" * 70)
    print()
    print("  CONCEITO:")
    print("  - DRE (competencia): reconhece receita em date_approved (BRT)")
    print("  - Caixa: dinheiro efetivamente disponivel em money_release_date")
    print("  - Diferenca = payments que cruzam mes (aprovados/liberados em meses diferentes)")
    print()

    cross_month = dre_data["cross_month"]

    # Dec aprovados, Jan caixa
    dec_jan = cross_month["dec_approved_jan_release"]
    total_dec_jan_gross = sum(p["amount"] for p in dec_jan)
    total_dec_jan_net = sum(p["net"] for p in dec_jan)
    print(f"  1. Vendas aprovadas em DEZ/2025 com liberacao em JAN/2026:")
    print(f"     (Estao no DRE de Dezembro, nao no DRE de Janeiro)")
    print(f"     Quantidade:       {len(dec_jan):>5} payments")
    print(f"     Receita bruta:    {fmt_brl(total_dec_jan_gross):>15}")
    print(f"     Net (caixa jan):  {fmt_brl(total_dec_jan_net):>15}")
    if len(dec_jan) <= 8:
        for p in dec_jan:
            print(f"       {p['payment_id']}  comp={p['competencia']}  "
                  f"release={p['money_release_date']}  R$ {p['amount']:,.2f}")
    else:
        for p in dec_jan[:5]:
            print(f"       {p['payment_id']}  comp={p['competencia']}  "
                  f"release={p['money_release_date']}  R$ {p['amount']:,.2f}")
        print(f"       ... e mais {len(dec_jan)-5} payments")
    print()

    # Jan aprovados, Fev caixa
    jan_feb = cross_month["jan_approved_feb_release"]
    total_jan_feb_gross = sum(p["amount"] for p in jan_feb)
    total_jan_feb_net = sum(p["net"] for p in jan_feb)
    print(f"  2. Vendas aprovadas em JAN/2026 com liberacao em FEV/2026+:")
    print(f"     (Estao no DRE de Janeiro, mas caixa so em Fevereiro)")
    print(f"     Quantidade:       {len(jan_feb):>5} payments")
    print(f"     Receita bruta:    {fmt_brl(total_jan_feb_gross):>15}")
    print(f"     Net (caixa fev):  {fmt_brl(total_jan_feb_net):>15}")
    if len(jan_feb) <= 8:
        for p in jan_feb:
            print(f"       {p['payment_id']}  comp={p['competencia']}  "
                  f"release={p['money_release_date']}  R$ {p['amount']:,.2f}")
    else:
        for p in jan_feb[:5]:
            print(f"       {p['payment_id']}  comp={p['competencia']}  "
                  f"release={p['money_release_date']}  R$ {p['amount']:,.2f}")
        print(f"       ... e mais {len(jan_feb)-5} payments")
    print()

    # Jan aprovados e liberados em Jan
    jan_jan = cross_month["jan_approved_jan_release"]
    total_jan_jan_gross = sum(p["amount"] for p in jan_jan)
    total_jan_jan_net = sum(p["net"] for p in jan_jan)
    print(f"  3. Vendas aprovadas E liberadas em JAN/2026 (DRE e caixa coincidem):")
    print(f"     Quantidade:       {len(jan_jan):>5} payments")
    print(f"     Receita bruta:    {fmt_brl(total_jan_jan_gross):>15}")
    print(f"     Net:              {fmt_brl(total_jan_jan_net):>15}")
    print()

    # Reconciliacao DRE vs Caixa
    print("  RECONCILIACAO COMPETENCIA vs CAIXA:")
    print()

    approved_jan_all = [s for s in simulated_jan
                        if s["action"] in ("APPROVED", "CHARGED_BACK_REIMBURSED")]
    total_dre_receita_jan = sum(s.get("amount", 0) for s in approved_jan_all)

    total_caixa_jan_gross = total_jan_jan_gross + total_dec_jan_gross
    total_caixa_jan_net = total_jan_jan_net + total_dec_jan_net

    print(f"  DRE Janeiro (competencia):")
    print(f"    Receita bruta jan aprovados: {fmt_brl(total_dre_receita_jan):>15}")
    print(f"    (inclui vendas de jan cujo caixa so vem em fevereiro)")
    print()
    print(f"  Caixa Janeiro (money_release_date=jan):")
    print(f"    Net de vendas jan/jan:       {fmt_brl(total_jan_jan_net):>15}")
    print(f"  + Net de vendas dez/jan:       {fmt_brl(total_dec_jan_net):>15}")
    print(f"  = Total net caixa jan:         {fmt_brl(total_caixa_jan_net):>15}")
    print()
    print(f"  Diferenca DRE - Caixa (bruto): {fmt_brl(total_dre_receita_jan - total_caixa_jan_gross):>15}")
    print(f"    Explicacao:")
    print(f"      R$ {total_jan_feb_gross:,.2f} em vendas de jan serao liberados em fev")
    print(f"      R$ {total_dec_jan_gross:,.2f} em vendas de dez entram no caixa de jan")
    print(f"      (mas estao no DRE de dezembro, nao de janeiro)")
    print()


# ══════════════════════════════════════════════════════════════════════════════
# SECAO: Sumario de payments
# ══════════════════════════════════════════════════════════════════════════════

def print_payments_summary(
    simulated_jan: list[dict],
    simulated_dec: list[dict],
    non_order_jan: list[dict],
) -> None:
    print("\n" + "=" * 70)
    print("  SUMARIO DE PAYMENTS — JANEIRO 2026")
    print("=" * 70)
    print()

    # Contagens por acao
    action_counts = defaultdict(int)
    for s in simulated_jan:
        action_counts[s["action"]] += 1

    print("  Payments com date_approved em JAN/2026:")
    for action, count in sorted(action_counts.items(), key=lambda x: -x[1]):
        print(f"    {action:<35} {count:>5}")

    print()

    approved_jan = [s for s in simulated_jan if s["action"] in ("APPROVED", "CHARGED_BACK_REIMBURSED")]
    refunded_jan = [s for s in simulated_jan if s["action"] == "REFUNDED"]
    skipped_jan = [s for s in simulated_jan if s["action"] == "SKIP"]
    non_order_count = len([s for s in non_order_jan if s["action"] == "NON_ORDER"
                           and s.get("competencia", "")[:7] == "2026-01"])

    # Totais financeiros para aprovados
    total_receita = sum(s.get("amount", 0) for s in approved_jan)
    total_comissao = sum(s.get("comissao", 0) for s in approved_jan)
    total_frete = sum(s.get("frete", 0) for s in approved_jan)
    total_net = sum(s.get("effective_net", s.get("net", 0)) for s in approved_jan)

    print(f"  Aprovados (APPROVED + CHARGED_BACK_REIMBURSED): {len(approved_jan)}")
    print(f"    Receita bruta:  {fmt_brl(total_receita)}")
    print(f"    Comissao ML:    {fmt_brl(total_comissao)}")
    print(f"    Frete seller:   {fmt_brl(total_frete)}")
    print(f"    Net total:      {fmt_brl(total_net)}")
    print()

    total_estorno = sum(s.get("estorno_receita", 0) for s in refunded_jan)
    total_estorno_taxa = sum(s.get("estorno_taxa", 0) for s in refunded_jan)
    print(f"  Devolvidos (REFUNDED): {len(refunded_jan)}")
    print(f"    Estorno receita: {fmt_brl(total_estorno)}")
    print(f"    Estorno taxa:    {fmt_brl(total_estorno_taxa)}")
    print()

    print(f"  Pulados (SKIP): {len(skipped_jan)}")
    skip_reasons = defaultdict(int)
    for s in skipped_jan:
        skip_reasons[s.get("skip_reason", "?")] += 1
    for reason, count in sorted(skip_reasons.items(), key=lambda x: -x[1]):
        print(f"    {reason:<45} {count:>5}")
    print()

    print(f"  Non-orders (NON_ORDER, comp=jan): {non_order_count}")
    print()

    # Dec aprovados
    dec_jan_cash = [s for s in simulated_dec
                    if s["action"] in ("APPROVED", "CHARGED_BACK_REIMBURSED")
                    and s.get("money_release_date", "")[:7] == "2026-01"]
    if dec_jan_cash:
        total_dec = sum(s.get("amount", 0) for s in dec_jan_cash)
        total_dec_net = sum(s.get("effective_net", s.get("net", 0)) for s in dec_jan_cash)
        print(f"  Aprovados em DEZ/2025 com release em JAN/2026: {len(dec_jan_cash)}")
        print(f"    Receita bruta (no DRE de dez): {fmt_brl(total_dec)}")
        print(f"    Net (caixa jan):               {fmt_brl(total_dec_net)}")
        print()


# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════

def run_seller_dre(seller_slug: str) -> dict:
    """Roda DRE completo para um seller. Retorna dict com resultado."""
    seller_upper = seller_slug.upper()
    extrato_file, cache_file = get_seller_paths(seller_slug)

    print()
    print("=" * 70)
    print(f"  DRE SIMULACAO — {seller_upper} — JANEIRO 2026 (COMPETENCIA)")
    print(f"  Seller: {seller_slug}")
    print(f"  Periodo DRE: {JAN_START} a {JAN_END}")
    print(f"  Criterio: date_approved em BRT (regime de competencia)")
    print("=" * 70)

    # ── Verificar arquivos ────────────────────────────────────────────────────
    if not cache_file.exists():
        print(f"\nERRO: Cache nao encontrado: {cache_file}")
        print("Execute reconciliation_jan2026.py primeiro para gerar o cache.")
        return {"error": "cache not found"}

    if not extrato_file.exists():
        print(f"\nERRO: Extrato nao encontrado: {extrato_file}")
        return {"error": "extrato not found"}

    # ── Fase 1: Carrega cache de payments ────────────────────────────────────
    print(f"\nCarregando payments do cache: {cache_file.name}")
    with open(cache_file) as f:
        cache_data = json.load(f)

    payments_all = cache_data["payments"]
    print(f"Total de payments no cache: {len(payments_all)}")
    counts = cache_data.get("counts", {})
    print(f"Contagens do cache: {counts}")

    # ── Filtra por periodo usando date_approved ───────────────────────────────
    payments_jan = [
        p for p in payments_all
        if _to_brt_date(p.get("date_approved") or p.get("date_created", ""))[:7] == "2026-01"
    ]
    print(f"Payments com date_approved (BRT) em jan/2026: {len(payments_jan)}")

    payments_dec = [
        p for p in payments_all
        if _to_brt_date(p.get("date_approved") or p.get("date_created", ""))[:7] == "2025-12"
    ]
    print(f"Payments com date_approved (BRT) em dez/2025: {len(payments_dec)}")

    # ── Fase 2: Simula todos os payments ─────────────────────────────────────
    print("\nSimulando processamento de payments (regime de competencia)...")

    simulated_jan = [simulate_payment_for_dre(p) for p in payments_jan]
    simulated_dec = [simulate_payment_for_dre(p) for p in payments_dec]
    simulated_all = [simulate_payment_for_dre(p) for p in payments_all]

    non_order_jan = [s for s in simulated_jan if s["action"] == "NON_ORDER"]
    simulated_dec_jan_cash = [
        s for s in simulated_dec
        if s["action"] in ("APPROVED", "CHARGED_BACK_REIMBURSED")
        and s.get("money_release_date", "")[:7] == "2026-01"
    ]

    print(f"  Simulated jan: {len(simulated_jan)} payments (date_approved BRT em jan)")
    print(f"  Non-orders jan: {len(non_order_jan)}")
    print(f"  Dec aprovados, release jan: {len(simulated_dec_jan_cash)}")

    # ── Fase 3: Parse do extrato ─────────────────────────────────────────────
    print(f"\nLendo extrato: {extrato_file.name}")
    extrato_summary, extrato_transactions = parse_extrato(extrato_file)
    jan_transactions = [tx for tx in extrato_transactions if tx["date"][:7] == "2026-01"]
    print(f"Total linhas extrato: {len(extrato_transactions)}")
    print(f"Linhas de janeiro: {len(jan_transactions)}")
    print(f"Extrato: creditos={fmt_brl(extrato_summary['credits'])}  "
          f"debitos={fmt_brl(extrato_summary['debits'])}")

    # ── Fase 4: Gaps do extrato (linhas nao cobertas pela API) ───────────────
    print("\nClassificando gaps do extrato...")
    extrato_gaps = classify_extrato_gaps(extrato_transactions, simulated_all)
    jan_gaps = [g for g in extrato_gaps if g.get("date", "")[:7] == "2026-01"]
    print(f"Gap lines identificadas: {len(extrato_gaps)} total, {len(jan_gaps)} em jan")

    # ── Fase 5: Caixa de janeiro ─────────────────────────────────────────────
    caixa_data = compute_caixa_from_extrato(extrato_transactions, simulated_all, extrato_gaps)

    # ── Fase 6: Constroi o DRE ───────────────────────────────────────────────
    print("\nConstruindo DRE por competencia...")
    dre_data = build_dre_from_simulated(
        simulated_jan,
        simulated_dec_jan_cash,
        non_order_jan,
        extrato_gaps,
    )

    # ── IMPRESSAO DOS RESULTADOS ──────────────────────────────────────────────
    print_payments_summary(simulated_jan, simulated_dec, non_order_jan)
    print_dre_report(
        dre_data, extrato_summary, extrato_transactions,
        caixa_data, simulated_jan, simulated_dec_jan_cash,
        non_order_jan, extrato_gaps,
        seller_slug=seller_slug,
    )
    print_category_breakdown(simulated_jan, non_order_jan, extrato_gaps, dre_data)
    print_dfc_report(extrato_transactions, extrato_summary, seller_slug)
    print_cross_month_analysis(dre_data, simulated_jan, simulated_dec)

    # ── RESUMO FINAL ──────────────────────────────────────────────────────────
    dre = dre_data["dre"]
    cross_month = dre_data["cross_month"]
    refunded_ids = dre_data.get("refunded_ids", set())

    total_receita_bruta = dre["venda_ml"]["total"]
    total_devolucao = dre["devolucao"]["total"]
    total_estorno_taxa = dre["estorno_taxa"]["total"]
    total_estorno_frete = dre["estorno_frete"]["total"]
    total_cashback = dre["cashback_ml"]["total"]
    total_outras = dre["outras_receitas"]["total"] + dre["deposito_avulso"]["total"]
    total_gap_income = dre["extrato_gap_income"]["total"]
    total_receitas = (total_receita_bruta - total_devolucao + total_estorno_taxa
                      + total_estorno_frete + total_cashback + total_outras + total_gap_income)
    total_despesas = (dre["comissao_ml"]["total"] + dre["frete_mercadoenvios"]["total"]
                      + dre["difal_icms"]["total"] + dre["subscription_saas"]["total"]
                      + dre["bill_payment"]["total"] + dre["collection_ml"]["total"]
                      + dre["extrato_gap_expense"]["total"])
    resultado = total_receitas - total_despesas

    print("\n" + "=" * 70)
    print(f"  RESUMO EXECUTIVO — DRE {seller_upper} JANEIRO 2026")
    print("=" * 70)
    print()
    print(f"  Receita bruta (1.1.1):          {fmt_brl(total_receita_bruta)}")
    print(f"  (-) Devolucoes (1.2.1):         {fmt_brl(-total_devolucao)}")
    if dre["devolucao"]["count"] > 0:
        from_proc = sum(1 for s in simulated_jan if s["action"] == "REFUNDED"
                       and s.get("competencia_estorno", "")[:7] == "2026-01")
        from_extrato = dre["devolucao"]["count"] - from_proc
        print(f"    (processor.py: {from_proc} refunds | extrato: {from_extrato} debito_divida/troca | sem duplic.)")
    print(f"  (+) Estornos de taxas (1.3.4):  {fmt_brl(total_estorno_taxa)}")
    if total_estorno_frete > 0:
        print(f"  (+) Estorno de Frete (1.3.7):   {fmt_brl(total_estorno_frete)}")
    if total_cashback > 0:
        print(f"  (+) Cashback ML (1.3.4):        {fmt_brl(total_cashback)}")
    if total_outras > 0:
        print(f"  (+) Outras receitas (1.4.2):    {fmt_brl(total_outras)}")
    if total_gap_income > 0:
        print(f"  (+) Outros creditos extrato:    {fmt_brl(total_gap_income)}")
    print(f"  ─────────────────────────────────────────────────────")
    print(f"  RECEITAS LIQUIDAS:              {fmt_brl(total_receitas)}")
    print()
    print(f"  (-) Comissoes ML (2.8.2):       {fmt_brl(-dre['comissao_ml']['total'])}")
    print(f"  (-) Frete seller (2.9.4):       {fmt_brl(-dre['frete_mercadoenvios']['total'])}")
    if dre["difal_icms"]["total"] > 0:
        print(f"  (-) DIFAL ICMS (2.2.3):         {fmt_brl(-dre['difal_icms']['total'])}")
    if dre["subscription_saas"]["total"] > 0:
        print(f"  (-) Assinaturas (2.14.12):      {fmt_brl(-dre['subscription_saas']['total'])}")
    if dre["bill_payment"]["total"] > 0:
        print(f"  (-) Boletos/Outras (2.x.x):     {fmt_brl(-dre['bill_payment']['total'])}")
    if dre["collection_ml"]["total"] > 0:
        print(f"  (-) Faturas/Cobrancas ML:       {fmt_brl(-dre['collection_ml']['total'])}")
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
    print(f"  Cross-month:")
    print(f"    DEZ aprovados, JAN caixa:     {len(cross_month['dec_approved_jan_release'])} payments"
          f"  ({fmt_brl(sum(p['amount'] for p in cross_month['dec_approved_jan_release']))})")
    print(f"    JAN aprovados, FEV caixa:     {len(cross_month['jan_approved_feb_release'])} payments"
          f"  ({fmt_brl(sum(p['amount'] for p in cross_month['jan_approved_feb_release']))})")
    print(f"    JAN aprovados, JAN caixa:     {len(cross_month['jan_approved_jan_release'])} payments"
          f"  ({fmt_brl(sum(p['amount'] for p in cross_month['jan_approved_jan_release']))})")
    print()
    print(f"  Extrato real jan (caixa):")
    print(f"    Total movimentacao:           {fmt_brl(sum(tx['amount'] for tx in jan_transactions))}")
    print(f"    Saldo inicial:                {fmt_brl(extrato_summary['initial_balance'])}")
    print(f"    Saldo final:                  {fmt_brl(extrato_summary['final_balance'])}")
    print()
    print("=" * 70)
    print(f"  Simulacao DRE {seller_upper} concluida.")
    print("  Criterio: date_approved convertido para BRT = regime de competencia.")
    print("=" * 70)
    print()

    return {"resultado": resultado, "receitas": total_receitas, "despesas": total_despesas}


def main():
    parser = argparse.ArgumentParser(description="DRE Simulacao — Janeiro 2026 (Competencia)")
    parser.add_argument("--seller", type=str, default=None,
                        help=f"Seller slug ({', '.join(ALL_SELLERS)})")
    parser.add_argument("--all", action="store_true",
                        help="Roda para todos os sellers")
    args = parser.parse_args()

    if args.all:
        sellers = ALL_SELLERS
    elif args.seller:
        sellers = [args.seller]
    else:
        sellers = ["141air"]

    results = {}
    for slug in sellers:
        result = run_seller_dre(slug)
        results[slug] = result

    if len(sellers) > 1:
        print("\n" + "=" * 70)
        print("  SUMARIO FINAL DRE — TODOS OS SELLERS")
        print("=" * 70)
        print()
        print(f"  {'Seller':<20} {'Receitas':>15} {'Despesas':>15} {'Resultado':>15}")
        print(f"  {'-'*20} {'-'*15} {'-'*15} {'-'*15}")
        for slug in sellers:
            r = results[slug]
            if "error" in r:
                print(f"  {slug:<20} {'ERRO':>15} {r['error']}")
            else:
                print(f"  {slug:<20} {fmt_brl(r['receitas']):>15} "
                      f"{fmt_brl(-r['despesas']):>15} {fmt_brl(r['resultado']):>15}")
        print("=" * 70 + "\n")


if __name__ == "__main__":
    main()
