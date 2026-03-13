#!/usr/bin/env python3
"""
DRE Simulacao — Parametrico por Mes (Competencia) — Agnostico
==============================================================
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
    cd levermoney
    python3 testes/simulate_dre.py --month 2026-01 [--seller SLUG]
    python3 testes/simulate_dre.py --month 2026-02 --seller 141air
    python3 testes/simulate_dre.py --month 2026-01 --all
"""

import sys
import os
import json
import logging
import argparse
from pathlib import Path
from datetime import date, datetime, timedelta, timezone
from calendar import monthrange
from collections import defaultdict

# -- Configuracao do projeto ---------------------------------------------------
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


# ==============================================================================
# MonthConfig — todas as constantes derivadas de um unico YYYY-MM
# ==============================================================================

MONTH_NAMES_PT = {
    1: "JANEIRO", 2: "FEVEREIRO", 3: "MARCO", 4: "ABRIL",
    5: "MAIO", 6: "JUNHO", 7: "JULHO", 8: "AGOSTO",
    9: "SETEMBRO", 10: "OUTUBRO", 11: "NOVEMBRO", 12: "DEZEMBRO",
}
MONTH_SHORT_PT = {
    1: "JAN", 2: "FEV", 3: "MAR", 4: "ABR",
    5: "MAI", 6: "JUN", 7: "JUL", 8: "AGO",
    9: "SET", 10: "OUT", 11: "NOV", 12: "DEZ",
}
MONTH_NAMES_PT_LOWER = {k: v.lower() for k, v in MONTH_NAMES_PT.items()}
MONTH_SHORT_PT_LOWER = {k: v.lower() for k, v in MONTH_SHORT_PT.items()}


class MonthConfig:
    """Derives all month-related constants from a single YYYY-MM string."""

    def __init__(self, year_month: str):
        parts = year_month.split("-")
        self.year = int(parts[0])
        self.month = int(parts[1])

        # Target month
        self.target_ym = f"{self.year:04d}-{self.month:02d}"
        _, last_day = monthrange(self.year, self.month)
        self.start = f"{self.target_ym}-01"
        self.end = f"{self.target_ym}-{last_day:02d}"
        self.num_days = last_day

        # Previous month
        if self.month == 1:
            prev_y, prev_m = self.year - 1, 12
        else:
            prev_y, prev_m = self.year, self.month - 1
        self.prev_ym = f"{prev_y:04d}-{prev_m:02d}"
        _, prev_last = monthrange(prev_y, prev_m)
        self.prev_start = f"{self.prev_ym}-01"
        self.prev_end = f"{self.prev_ym}-{prev_last:02d}"

        # Next month
        if self.month == 12:
            next_y, next_m = self.year + 1, 1
        else:
            next_y, next_m = self.year, self.month + 1
        self.next_ym = f"{next_y:04d}-{next_m:02d}"

        # Display names — target
        self.month_name = MONTH_NAMES_PT[self.month]
        self.month_short = MONTH_SHORT_PT[self.month]
        self.month_name_lower = MONTH_NAMES_PT_LOWER[self.month]
        self.month_year = f"{self.month_name} {self.year}"          # "JANEIRO 2026"
        self.month_short_lower = MONTH_SHORT_PT_LOWER[self.month]      # "jan"
        self.short_year = f"{self.month_short}/{self.year}"          # "JAN/2026"
        self.short_year_lower = self.short_year.lower()              # "jan/2026"

        # Display names — previous
        self.prev_name = MONTH_NAMES_PT[prev_m]
        self.prev_short = MONTH_SHORT_PT[prev_m]
        self.prev_name_lower = MONTH_NAMES_PT_LOWER[prev_m]
        self.prev_year_display = f"{self.prev_name} {prev_y}"
        self.prev_short_year = f"{self.prev_short}/{prev_y}"         # "DEZ/2025"
        self.prev_year_int = prev_y

        # Display names — next
        self.next_name = MONTH_NAMES_PT[next_m]
        self.next_short = MONTH_SHORT_PT[next_m]
        self.next_short_year = f"{self.next_short}/{next_y}"         # "FEV/2026"
        self.next_name_lower = MONTH_NAMES_PT_LOWER[next_m]

        # Date list for the target month
        self.dates = [
            (date(self.year, self.month, 1) + timedelta(days=i)).isoformat()
            for i in range(last_day)
        ]

        # Extrato saldo labels
        self.initial_balance_label = f"{prev_last:02d}/{prev_m:02d}/{prev_y}"
        self.final_balance_label = f"{last_day:02d}/{self.month:02d}/{self.year}"

        # Directories
        self.cache_dir = PROJECT_ROOT / "testes" / f"cache_{MONTH_SHORT_PT_LOWER[self.month]}{self.year}"
        self.extratos_dir = PROJECT_ROOT / "testes" / "extratos"


BRT = timezone(timedelta(hours=-3))


# ==============================================================================
# Discovery — auto-descoberta de sellers e extratos
# ==============================================================================

def discover_sellers(cfg: MonthConfig) -> dict[str, str]:
    """
    Auto-discover sellers from extrato files for the configured month.
    Returns dict: slug -> extrato filename.
    Scans for files matching 'extrato {month_name_lower} *.csv'.
    """
    pattern = f"extrato {cfg.month_name_lower} *.csv"
    result = {}
    for f in sorted(cfg.extratos_dir.glob(pattern)):
        parts = f.stem.split(" ", 2)
        if len(parts) >= 3:
            display_name = parts[2]
            slug = display_name.lower().replace(" ", "-")
            result[slug] = f.name
    return result


def find_extrato_file(seller_slug: str, cfg: MonthConfig) -> Path | None:
    """Find extrato file for seller, trying multiple matching strategies."""
    discovered = discover_sellers(cfg)

    # Exact match
    if seller_slug in discovered:
        return cfg.extratos_dir / discovered[seller_slug]

    # Match without hyphens
    slug_clean = seller_slug.replace("-", "")
    for slug, fname in discovered.items():
        if slug.replace("-", "") == slug_clean:
            return cfg.extratos_dir / fname

    # Partial match (slug contained in filename or vice-versa)
    for slug, fname in discovered.items():
        if slug_clean in slug.replace("-", "") or slug.replace("-", "") in slug_clean:
            return cfg.extratos_dir / fname

    return None


def get_seller_paths(seller_slug: str, cfg: MonthConfig) -> tuple[Path, Path]:
    """Retorna (extrato_file, cache_file) para o seller."""
    extrato_file = find_extrato_file(seller_slug, cfg)
    if not extrato_file:
        available = discover_sellers(cfg)
        print(f"ERRO: Seller '{seller_slug}' nao tem extrato para {cfg.month_name_lower} {cfg.year}.")
        if available:
            print(f"Sellers disponiveis: {', '.join(available.keys())}")
        else:
            print(f"Nenhum extrato encontrado em: {cfg.extratos_dir}")
            print(f"Padrao esperado: 'extrato {cfg.month_name_lower} <NomeSeller>.csv'")
        sys.exit(1)

    cache_file = cfg.cache_dir / f"{seller_slug}_payments.json"
    return extrato_file, cache_file


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


# ==============================================================================
# AUXILIARES — Formatacao monetaria
# ==============================================================================

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


# ==============================================================================
# FASE 1 — Parse do extrato real
# ==============================================================================

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


# ==============================================================================
# FASE 2 — Simulacao do processor (logica de competencia)
# ==============================================================================

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

    # -- Sem order_id: classificar como non-order --------------------------------
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

    # -- Filtros de skip para orders ---------------------------------------------
    if payment.get("description") == "marketplace_shipment":
        return {**base, "action": "SKIP", "skip_reason": "marketplace_shipment"}

    if (payment.get("collector") or {}).get("id") is not None:
        return {**base, "action": "SKIP", "skip_reason": "compra (collector_id)"}

    # -- Dispatch por status -----------------------------------------------------
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


# ==============================================================================
# FASE 3 — Construcao do DRE por competencia
# ==============================================================================

def build_dre_from_simulated(
    simulated_target: list[dict],
    simulated_prev_target_cash: list[dict],
    non_order_target: list[dict],
    extrato_gaps: list[dict],
    cfg: MonthConfig,
) -> dict:
    """
    Constroi o DRE do mes-alvo por competencia.

    Mapeamento correto de tipos do extrato para linhas DRE:
      dinheiro_retido      -> SKIP (cash flow only; nets to zero com reembolso_reclamacoes)
      liberacao_cancelada  -> SKIP (reversao interna, neutro)
      reembolso_disputa    -> 1.3.4 Estornos de Taxas (dinheiro retido devolvido ao ganhar mediacao)
      reembolso_generico   -> 1.3.4 Estornos de Taxas
      bonus_envio          -> 1.3.7 Estorno de Frete
      entrada_dinheiro     -> 1.4.2 Outras Receitas Eventuais
      deposito_avulso      -> 1.4.2 Outras Receitas Eventuais
      debito_divida_disputa -> 1.2.1 Devolucoes (SKIP se payment ja foi refunded pelo processor)
      debito_troca         -> 1.2.1 Devolucoes
      difal                -> 2.2.3 DIFAL (Diferencial de Aliquota)
      faturas_ml           -> 2.8.2 Comissoes Marketplace
      debito_envio_ml      -> 2.9.4 MercadoEnvios
      subscription (non-order) -> 2.14.12 Assinaturas
    """

    dre = {
        # RECEITAS (valores positivos)
        "venda_ml":             {"total": 0.0, "count": 0},
        "devolucao":            {"total": 0.0, "count": 0},
        "estorno_taxa":         {"total": 0.0, "count": 0},
        "estorno_frete":        {"total": 0.0, "count": 0},
        "cashback_ml":          {"total": 0.0, "count": 0},
        "outras_receitas":      {"total": 0.0, "count": 0},
        "deposito_avulso":      {"total": 0.0, "count": 0},
        "extrato_gap_income":   {"total": 0.0, "count": 0},

        # DESPESAS (valores positivos, apresentados como negativos no DRE)
        "comissao_ml":          {"total": 0.0, "count": 0},
        "frete_mercadoenvios":  {"total": 0.0, "count": 0},
        "difal_icms":           {"total": 0.0, "count": 0},
        "subscription_saas":    {"total": 0.0, "count": 0},
        "bill_payment":         {"total": 0.0, "count": 0},
        "collection_ml":        {"total": 0.0, "count": 0},
        "extrato_gap_expense":  {"total": 0.0, "count": 0},
    }

    # Cross-month tracking
    cross_month = {
        "prev_approved_target_release": [],   # DRE prev, caixa target
        "target_approved_next_release": [],   # DRE target, caixa next
        "target_approved_target_release": [], # DRE target, caixa target
    }

    # IDs de payments refunded no mes-alvo pelo processor (para evitar dupla contagem com extrato)
    refunded_payment_ids: set[str] = set()
    for sim in simulated_target:
        if sim["action"] == "REFUNDED":
            refunded_payment_ids.add(str(sim["payment_id"]))

    # -- ORDERS APROVADOS no mes-alvo (DRE target) --------------------------------
    for sim in simulated_target:
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
        if release and release[:7] == cfg.next_ym:
            cross_month["target_approved_next_release"].append({
                "payment_id": sim["payment_id"],
                "amount": amount,
                "net": sim.get("net", 0),
                "competencia": sim.get("competencia"),
                "money_release_date": release,
            })
        elif release and release[:7] == cfg.target_ym:
            cross_month["target_approved_target_release"].append({
                "payment_id": sim["payment_id"],
                "amount": amount,
                "net": sim.get("net", 0),
                "competencia": sim.get("competencia"),
                "money_release_date": release,
            })

    # -- DEVOLUCOES no mes-alvo (competencia do ESTORNO no mes-alvo) ----------------
    for sim in simulated_target:
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
        if competencia_original and competencia_original[:7] == cfg.target_ym:
            dre["venda_ml"]["total"] += amount
            dre["venda_ml"]["count"] += 1
            if comissao > 0:
                dre["comissao_ml"]["total"] += comissao
                dre["comissao_ml"]["count"] += 1
            if frete > 0:
                dre["frete_mercadoenvios"]["total"] += frete
                dre["frete_mercadoenvios"]["count"] += 1

        # Estorno da receita (lancado na competencia do estorno)
        if competencia_estorno and competencia_estorno[:7] == cfg.target_ym:
            dre["devolucao"]["total"] += estorno_receita
            dre["devolucao"]["count"] += 1

            if estorno_taxa > 0:
                dre["estorno_taxa"]["total"] += estorno_taxa
                dre["estorno_taxa"]["count"] += 1

    # -- NON-ORDERS no mes-alvo (classificados pelo expense_classifier) -----------
    for sim in non_order_target:
        if sim["action"] != "NON_ORDER":
            continue

        amount = abs(sim.get("amount", 0.0))
        direction = sim.get("direction", "")
        exp_type = sim.get("expense_type", "")
        competencia = sim.get("competencia", "")

        if not competencia or competencia[:7] != cfg.target_ym:
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

    # -- GAPS DO EXTRATO — CATEGORIZACAO CORRIGIDA --------------------------------
    for gap in extrato_gaps:
        amount = abs(gap.get("amount", 0.0))
        direction = gap.get("direction")
        exp_type = gap.get("expense_type", "")
        gap_date = gap.get("date", "")
        ref_id = gap.get("reference_id", "")

        if not gap_date or gap_date[:7] != cfg.target_ym:
            continue

        # Cash-flow-only: neutros no DRE (retido + reembolso se anulam)
        if exp_type in ("dinheiro_retido", "liberacao_cancelada"):
            continue

        if direction == "income":
            if exp_type in ("reembolso_disputa", "reembolso_generico"):
                dre["estorno_taxa"]["total"] += amount
                dre["estorno_taxa"]["count"] += 1
            elif exp_type == "bonus_envio":
                dre["estorno_frete"]["total"] += amount
                dre["estorno_frete"]["count"] += 1
            elif exp_type in ("entrada_dinheiro", "deposito_avulso"):
                dre["outras_receitas"]["total"] += amount
                dre["outras_receitas"]["count"] += 1
            else:
                dre["extrato_gap_income"]["total"] += amount
                dre["extrato_gap_income"]["count"] += 1

        elif direction == "expense":
            if exp_type == "difal":
                dre["difal_icms"]["total"] += amount
                dre["difal_icms"]["count"] += 1
            elif exp_type in ("debito_divida_disputa", "debito_troca"):
                if ref_id in refunded_payment_ids:
                    continue
                dre["devolucao"]["total"] += amount
                dre["devolucao"]["count"] += 1
            elif exp_type == "faturas_ml":
                dre["collection_ml"]["total"] += amount
                dre["collection_ml"]["count"] += 1
            elif exp_type == "debito_envio_ml":
                dre["frete_mercadoenvios"]["total"] += amount
                dre["frete_mercadoenvios"]["count"] += 1
            else:
                dre["extrato_gap_expense"]["total"] += amount
                dre["extrato_gap_expense"]["count"] += 1

    # -- ORDERS do mes ANTERIOR com liberacao no mes-alvo (DRE prev, caixa target) --
    for sim in simulated_prev_target_cash:
        if sim["action"] not in ("APPROVED", "CHARGED_BACK_REIMBURSED"):
            continue
        cross_month["prev_approved_target_release"].append({
            "payment_id": sim["payment_id"],
            "amount": sim.get("amount", 0),
            "net": sim.get("net", 0),
            "competencia": sim.get("competencia"),
            "money_release_date": sim.get("money_release_date"),
        })

    return {"dre": dre, "cross_month": cross_month, "refunded_ids": refunded_payment_ids}


# ==============================================================================
# FASE 4 — Analise do Extrato para Caixa
# ==============================================================================

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
    cfg: MonthConfig,
) -> dict:
    """
    Computa o caixa do mes-alvo a partir do extrato real.
    O caixa = soma de TODAS as linhas do extrato no periodo.
    """
    target_txs = [tx for tx in transactions if tx["date"][:7] == cfg.target_ym]

    total_extrato = sum(tx["amount"] for tx in target_txs)
    total_credits = sum(tx["amount"] for tx in target_txs if tx["amount"] > 0)
    total_debits = sum(tx["amount"] for tx in target_txs if tx["amount"] < 0)

    sim_by_id = {str(s["payment_id"]): s for s in simulated_all}
    total_liberacoes = 0.0
    liberacao_count = 0
    for tx in target_txs:
        cat = classify_extrato_category(tx["type"])
        if cat == "liberacao":
            sim = sim_by_id.get(tx["reference_id"])
            if sim and sim["action"] in ("APPROVED", "CHARGED_BACK_REIMBURSED"):
                total_liberacoes += tx["amount"]
                liberacao_count += 1

    total_gaps = sum(gap.get("amount", 0) for gap in extrato_gaps
                     if gap.get("date", "")[:7] == cfg.target_ym)
    gap_count = len([g for g in extrato_gaps if g.get("date", "")[:7] == cfg.target_ym])

    return {
        "total_extrato": round(total_extrato, 2),
        "total_credits": round(total_credits, 2),
        "total_debits": round(total_debits, 2),
        "total_liberacoes": round(total_liberacoes, 2),
        "liberacao_count": liberacao_count,
        "total_gaps": round(total_gaps, 2),
        "gap_count": gap_count,
        "total_target_lines": len(target_txs),
    }


# ==============================================================================
# FASE 5 — Classificacao dos gaps do extrato
# ==============================================================================

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

        if sim and sim["action"] != "SKIP":
            continue

        expense_type, direction, ca_cat_uuid = _classify_extrato_line(tx["type"])

        if expense_type is None and direction is None:
            continue

        gaps.append({
            **tx,
            "expense_type": expense_type,
            "direction": direction,
            "ca_category": ca_cat_uuid,
        })

    return gaps


# ==============================================================================
# IMPRESSAO DO DRE
# ==============================================================================

def print_dre_report(
    dre_data: dict,
    extrato_summary: dict,
    extrato_transactions: list[dict],
    caixa_data: dict,
    simulated_target: list[dict],
    simulated_prev_target_cash: list[dict],
    non_order_target: list[dict],
    extrato_gaps: list[dict],
    seller_slug: str,
    cfg: MonthConfig,
) -> None:
    """Imprime o DRE formatado."""
    dre = dre_data["dre"]
    cross_month = dre_data["cross_month"]
    seller_upper = seller_slug.upper()

    # -- Calcula totais ----------------------------------------------------------
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

    # -- Calcula caixa (aprovados liberados no mes-alvo) --------------------------
    approved_sims = [s for s in simulated_target if s["action"] in ("APPROVED", "CHARGED_BACK_REIMBURSED")]
    total_net_aprovados_target = sum(s.get("effective_net", s.get("net", 0)) for s in approved_sims
                                     if s.get("money_release_date", "")[:7] == cfg.target_ym)

    prev_net_target_cash = sum(s.get("effective_net", s.get("net", 0))
                               for s in cross_month["prev_approved_target_release"])

    # -- IMPRIMIR DRE ------------------------------------------------------------
    W = 70
    line = "=" * W

    dre_title = f"DRE - {seller_upper} - {cfg.month_year} (COMPETENCIA)"
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
    dre_note(f"({dre['venda_ml']['count']} vendas aprovadas em {cfg.short_year_lower} por data_approved)")
    if total_devolucao > 0:
        dre_row("1.2.1  (-) Devolucoes e Cancelamentos", -total_devolucao)
        dre_note(f"({dre['devolucao']['count']} devolucoes com estorno em {cfg.short_year_lower})")
    if total_estorno_taxa > 0:
        dre_row("1.3.4  (+) Estornos de Taxas",         total_estorno_taxa)
        dre_note(f"({dre['estorno_taxa']['count']} estornos de taxa - devolucoes totais)")
    if total_estorno_frete > 0:
        dre_row("1.3.7  (+) Estorno de Frete / Subsidio ML", total_estorno_frete)
    if total_cashback > 0:
        dre_row("1.3.4  (+) Cashback / Ressarcimento ML", total_cashback)
        dre_note(f"({dre['cashback_ml']['count']} cashbacks)")
    if total_outras > 0:
        dre_row("1.4.2  (+) Outras Receitas",             total_outras)
    if total_gap_income > 0:
        dre_row("1.3.x  (+) Outros Creditos (extrato)",   total_gap_income)
        dre_note(f"({dre['extrato_gap_income']['count']} linhas do extrato sem match na API)")

    print("|" + " " * W + "|")
    print("|" + f"{'':5}{'─' * 45}{'─'*20}" + "|")
    dre_row("RECEITAS LIQUIDAS",                     total_receitas_liquidas, indent=5)
    print("|" + " " * W + "|")

    print("|" + f"  {'2. DESPESAS':<{W-2}}" + "|")
    print("|" + " " * W + "|")

    dre_row("2.8.2  Comissoes Marketplace",           -total_comissao)
    dre_note(f"({dre['comissao_ml']['count']} vendas)")
    dre_row("2.9.4  MercadoEnvios (Frete Seller)",   -total_frete)
    dre_note(f"({dre['frete_mercadoenvios']['count']} envios)")
    if total_difal > 0:
        dre_row("2.2.3  DIFAL (Diferencial de Aliquota)", -total_difal)
        dre_note(f"({dre['difal_icms']['count']} debitos DIFAL)")
    if total_sub > 0:
        dre_row("2.14.12 Assinaturas SaaS",             -total_sub)
        dre_note(f"({dre['subscription_saas']['count']} assinaturas)")
    if total_boleto > 0:
        dre_row("2.x.x  Boletos / Outras Despesas",     -total_boleto)
        dre_note(f"({dre['bill_payment']['count']} boletos)")
    if total_cobranca > 0:
        dre_row("2.8.2  Faturas / Cobrancas ML",        -total_cobranca)
        dre_note(f"({dre['collection_ml']['count']} cobranças)")
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
    print("|" + f"  {'MEMO: Fluxo de Caixa (money_release_date em ' + cfg.month_name.title() + ')':<{W-2}}" + "|")
    print("|" + " " * W + "|")

    # Caixa
    extrato_total_target = sum(tx["amount"] for tx in extrato_transactions
                               if tx["date"][:7] == cfg.target_ym)

    dre_row(f"Net vendas {cfg.short_year_lower} aprovadas, release={cfg.month_short_lower}:", total_net_aprovados_target)
    dre_row(f"+ Net vendas {cfg.prev_short_year.lower()} aprovadas, release={cfg.month_short_lower}:", prev_net_target_cash)
    caixa_total = total_net_aprovados_target + prev_net_target_cash
    dre_row("= Total net caixa vendas (API):",         caixa_total)
    print("|" + " " * W + "|")
    dre_row(f"Extrato real (total movimentacao {cfg.month_short_lower}):", extrato_total_target)
    dre_note(f"  Creditos: {fmt_brl(extrato_summary['credits'])}  "
             f"Debitos: {fmt_brl(extrato_summary['debits'])}")
    dre_note("  Nota: Extrato inclui transferencias/PIX que nao sao DRE.")
    dre_note("  DRE (competencia) e Caixa diferem pelo cross-month (veja secao abaixo).")
    print("|" + " " * W + "|")
    print("+" + line + "+")
    print()


# ==============================================================================
# SECAO: Breakdown detalhado por categoria
# ==============================================================================

def print_category_breakdown(
    simulated_target: list[dict],
    non_order_target: list[dict],
    extrato_gaps: list[dict],
    dre_data: dict,
    cfg: MonthConfig,
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
    for sim in non_order_target:
        if sim["action"] == "NON_ORDER" and sim.get("competencia", "")[:7] == cfg.target_ym:
            exp_type = sim.get("expense_type", "?")
            direction = sim.get("direction", "?")
            non_order_types[f"{exp_type} ({direction})"]["count"] += 1
            non_order_types[f"{exp_type} ({direction})"]["total"] += abs(sim.get("amount", 0))

    if non_order_types:
        print(f"  NON-ORDERS CLASSIFICADOS (date_approved em {cfg.short_year_lower}):")
        print(f"  {'Tipo (direcao)':<45} {'Qtd':>5}  {'Total (R$)':>15}")
        print(f"  {'-'*45} {'-'*5}  {'-'*15}")
        for key, v in sorted(non_order_types.items()):
            print(f"  {key:<45} {v['count']:>5}  {v['total']:>14,.2f}")
        print()

    # Gaps do extrato
    gap_types = defaultdict(lambda: {"count": 0, "total": 0.0})
    for gap in extrato_gaps:
        if gap.get("date", "")[:7] == cfg.target_ym:
            exp_type = gap.get("expense_type", "?")
            direction = gap.get("direction", "?")
            gap_types[f"{exp_type} ({direction})"]["count"] += 1
            gap_types[f"{exp_type} ({direction})"]["total"] += abs(gap.get("amount", 0))

    if gap_types:
        print(f"  GAPS DO EXTRATO CLASSIFICADOS ({cfg.short_year_lower}):")
        print(f"  {'Tipo (direcao)':<45} {'Qtd':>5}  {'Total (R$)':>15}")
        print(f"  {'-'*45} {'-'*5}  {'-'*15}")
        for key, v in sorted(gap_types.items()):
            print(f"  {key:<45} {v['count']:>5}  {v['total']:>14,.2f}")
        print()


# ==============================================================================
# SECAO: DFC — Demonstrativo de Fluxo de Caixa
# ==============================================================================

def print_dfc_report(
    extrato_transactions: list[dict],
    extrato_summary: dict,
    seller_slug: str,
    cfg: MonthConfig,
) -> None:
    """Imprime o DFC (Demonstrativo de Fluxo de Caixa) do mes-alvo."""
    target_txs = [tx for tx in extrato_transactions if tx["date"][:7] == cfg.target_ym]

    # Classifica cada linha do extrato
    dfc = {
        "liberacao_vendas":    {"total": 0.0, "count": 0},
        "reembolso":           {"total": 0.0, "count": 0},
        "bonus_envio":         {"total": 0.0, "count": 0},
        "deposito_avulso":     {"total": 0.0, "count": 0},
        "entrada_outros":      {"total": 0.0, "count": 0},
        "difal":               {"total": 0.0, "count": 0},
        "faturas_ml":          {"total": 0.0, "count": 0},
        "debito_envio_ml":     {"total": 0.0, "count": 0},
        "dinheiro_retido":     {"total": 0.0, "count": 0},
        "liberacao_cancelada": {"total": 0.0, "count": 0},
        "debito_disputa":      {"total": 0.0, "count": 0},
        "pagamento_conta":     {"total": 0.0, "count": 0},
        "subscription_mp":     {"total": 0.0, "count": 0},
        "pix_saida":           {"total": 0.0, "count": 0},
        "pix_entrada":         {"total": 0.0, "count": 0},
        "transferencia_saldo": {"total": 0.0, "count": 0},
        "nao_identificado":    {"total": 0.0, "count": 0},
    }

    for tx in target_txs:
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

    month_short_lower = MONTH_SHORT_PT_LOWER[cfg.month]

    print("\n" + "=" * 70)
    print(f"  DFC — {seller_slug.upper()} — DEMONSTRATIVO DE FLUXO DE CAIXA — {cfg.short_year}")
    print("=" * 70)
    print()
    print(f"  Saldo inicial ({cfg.initial_balance_label}):     {fmt_brl(extrato_summary['initial_balance'])}")
    print()

    def dfc_row(label: str, value: float, indent: int = 4) -> None:
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
    print(f"  Saldo final ({cfg.final_balance_label}):       {fmt_brl(extrato_summary['final_balance'])}")
    print()
    print(f"  Nota: 'dinheiro_retido' e 'reembolso_reclamacoes' se anulam no DFC total.")
    print(f"  O DRE exclui essas linhas pois sao neutras economicamente.")
    print("=" * 70)
    print()



# ==============================================================================
# SECAO: Analise cross-month
# ==============================================================================

def print_cross_month_analysis(
    dre_data: dict,
    simulated_target: list[dict],
    simulated_prev: list[dict],
    cfg: MonthConfig,
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

    # Prev aprovados, Target caixa
    prev_target = cross_month["prev_approved_target_release"]
    total_prev_target_gross = sum(p["amount"] for p in prev_target)
    total_prev_target_net = sum(p["net"] for p in prev_target)
    print(f"  1. Vendas aprovadas em {cfg.prev_short_year} com liberacao em {cfg.short_year}:")
    print(f"     (Estao no DRE de {cfg.prev_name.title()}, nao no DRE de {cfg.month_name.title()})")
    print(f"     Quantidade:       {len(prev_target):>5} payments")
    print(f"     Receita bruta:    {fmt_brl(total_prev_target_gross):>15}")
    print(f"     Net (caixa {cfg.month_short_lower}):  {fmt_brl(total_prev_target_net):>15}")
    if len(prev_target) <= 8:
        for p in prev_target:
            print(f"       {p['payment_id']}  comp={p['competencia']}  "
                  f"release={p['money_release_date']}  R$ {p['amount']:,.2f}")
    else:
        for p in prev_target[:5]:
            print(f"       {p['payment_id']}  comp={p['competencia']}  "
                  f"release={p['money_release_date']}  R$ {p['amount']:,.2f}")
        print(f"       ... e mais {len(prev_target)-5} payments")
    print()

    # Target aprovados, Next caixa
    target_next = cross_month["target_approved_next_release"]
    total_target_next_gross = sum(p["amount"] for p in target_next)
    total_target_next_net = sum(p["net"] for p in target_next)
    print(f"  2. Vendas aprovadas em {cfg.short_year} com liberacao em {cfg.next_short_year}+:")
    print(f"     (Estao no DRE de {cfg.month_name.title()}, mas caixa so em {cfg.next_name.title()})")
    print(f"     Quantidade:       {len(target_next):>5} payments")
    print(f"     Receita bruta:    {fmt_brl(total_target_next_gross):>15}")
    print(f"     Net (caixa {cfg.next_name_lower[:3]}):  {fmt_brl(total_target_next_net):>15}")
    if len(target_next) <= 8:
        for p in target_next:
            print(f"       {p['payment_id']}  comp={p['competencia']}  "
                  f"release={p['money_release_date']}  R$ {p['amount']:,.2f}")
    else:
        for p in target_next[:5]:
            print(f"       {p['payment_id']}  comp={p['competencia']}  "
                  f"release={p['money_release_date']}  R$ {p['amount']:,.2f}")
        print(f"       ... e mais {len(target_next)-5} payments")
    print()

    # Target aprovados e liberados no Target
    target_target = cross_month["target_approved_target_release"]
    total_target_target_gross = sum(p["amount"] for p in target_target)
    total_target_target_net = sum(p["net"] for p in target_target)
    print(f"  3. Vendas aprovadas E liberadas em {cfg.short_year} (DRE e caixa coincidem):")
    print(f"     Quantidade:       {len(target_target):>5} payments")
    print(f"     Receita bruta:    {fmt_brl(total_target_target_gross):>15}")
    print(f"     Net:              {fmt_brl(total_target_target_net):>15}")
    print()

    # Reconciliacao DRE vs Caixa
    print("  RECONCILIACAO COMPETENCIA vs CAIXA:")
    print()

    approved_target_all = [s for s in simulated_target
                           if s["action"] in ("APPROVED", "CHARGED_BACK_REIMBURSED")]
    total_dre_receita_target = sum(s.get("amount", 0) for s in approved_target_all)

    total_caixa_target_gross = total_target_target_gross + total_prev_target_gross
    total_caixa_target_net = total_target_target_net + total_prev_target_net

    print(f"  DRE {cfg.month_name.title()} (competencia):")
    print(f"    Receita bruta {cfg.month_short_lower} aprovados: {fmt_brl(total_dre_receita_target):>15}")
    print(f"    (inclui vendas de {cfg.month_short_lower} cujo caixa so vem em {cfg.next_name_lower})")
    print()
    print(f"  Caixa {cfg.month_name.title()} (money_release_date={cfg.month_short_lower}):")
    print(f"    Net de vendas {cfg.month_short_lower}/{cfg.month_short_lower}:       {fmt_brl(total_target_target_net):>15}")
    print(f"  + Net de vendas {cfg.prev_short.lower()}/{cfg.month_short_lower}:       {fmt_brl(total_prev_target_net):>15}")
    print(f"  = Total net caixa {cfg.month_short_lower}:         {fmt_brl(total_caixa_target_net):>15}")
    print()
    print(f"  Diferenca DRE - Caixa (bruto): {fmt_brl(total_dre_receita_target - total_caixa_target_gross):>15}")
    print(f"    Explicacao:")
    print(f"      R$ {total_target_next_gross:,.2f} em vendas de {cfg.month_short_lower} serao liberados em {cfg.next_name_lower[:3]}")
    print(f"      R$ {total_prev_target_gross:,.2f} em vendas de {cfg.prev_short.lower()} entram no caixa de {cfg.month_short_lower}")
    print(f"      (mas estao no DRE de {cfg.prev_name_lower}, nao de {cfg.month_name_lower})")
    print()



# ==============================================================================
# SECAO: Sumario de payments
# ==============================================================================

def print_payments_summary(
    simulated_target: list[dict],
    simulated_prev: list[dict],
    non_order_target: list[dict],
    cfg: MonthConfig,
) -> None:
    print("\n" + "=" * 70)
    print(f"  SUMARIO DE PAYMENTS — {cfg.month_year}")
    print("=" * 70)
    print()

    # Contagens por acao
    action_counts = defaultdict(int)
    for s in simulated_target:
        action_counts[s["action"]] += 1

    print(f"  Payments com date_approved em {cfg.short_year}:")
    for action, count in sorted(action_counts.items(), key=lambda x: -x[1]):
        print(f"    {action:<35} {count:>5}")

    print()

    approved_target = [s for s in simulated_target if s["action"] in ("APPROVED", "CHARGED_BACK_REIMBURSED")]
    refunded_target = [s for s in simulated_target if s["action"] == "REFUNDED"]
    skipped_target = [s for s in simulated_target if s["action"] == "SKIP"]
    non_order_count = len([s for s in non_order_target if s["action"] == "NON_ORDER"
                           and s.get("competencia", "")[:7] == cfg.target_ym])

    # Totais financeiros para aprovados
    total_receita = sum(s.get("amount", 0) for s in approved_target)
    total_comissao = sum(s.get("comissao", 0) for s in approved_target)
    total_frete = sum(s.get("frete", 0) for s in approved_target)
    total_net = sum(s.get("effective_net", s.get("net", 0)) for s in approved_target)

    print(f"  Aprovados (APPROVED + CHARGED_BACK_REIMBURSED): {len(approved_target)}")
    print(f"    Receita bruta:  {fmt_brl(total_receita)}")
    print(f"    Comissao ML:    {fmt_brl(total_comissao)}")
    print(f"    Frete seller:   {fmt_brl(total_frete)}")
    print(f"    Net total:      {fmt_brl(total_net)}")
    print()

    total_estorno = sum(s.get("estorno_receita", 0) for s in refunded_target)
    total_estorno_taxa = sum(s.get("estorno_taxa", 0) for s in refunded_target)
    print(f"  Devolvidos (REFUNDED): {len(refunded_target)}")
    print(f"    Estorno receita: {fmt_brl(total_estorno)}")
    print(f"    Estorno taxa:    {fmt_brl(total_estorno_taxa)}")
    print()

    print(f"  Pulados (SKIP): {len(skipped_target)}")
    skip_reasons = defaultdict(int)
    for s in skipped_target:
        skip_reasons[s.get("skip_reason", "?")] += 1
    for reason, count in sorted(skip_reasons.items(), key=lambda x: -x[1]):
        print(f"    {reason:<45} {count:>5}")
    print()

    print(f"  Non-orders (NON_ORDER, comp={cfg.month_short_lower}): {non_order_count}")
    print()

    # Prev aprovados
    prev_target_cash = [s for s in simulated_prev
                        if s["action"] in ("APPROVED", "CHARGED_BACK_REIMBURSED")
                        and s.get("money_release_date", "")[:7] == cfg.target_ym]
    if prev_target_cash:
        total_prev = sum(s.get("amount", 0) for s in prev_target_cash)
        total_prev_net = sum(s.get("effective_net", s.get("net", 0)) for s in prev_target_cash)
        print(f"  Aprovados em {cfg.prev_short_year} com release em {cfg.short_year}: {len(prev_target_cash)}")
        print(f"    Receita bruta (no DRE de {cfg.prev_short.lower()}): {fmt_brl(total_prev)}")
        print(f"    Net (caixa {cfg.month_short_lower}):               {fmt_brl(total_prev_net)}")
        print()


# ==============================================================================
# MAIN
# ==============================================================================

def run_seller_dre(seller_slug: str, cfg: MonthConfig) -> dict:
    """Roda DRE completo para um seller. Retorna dict com resultado."""
    seller_upper = seller_slug.upper()
    extrato_file, cache_file = get_seller_paths(seller_slug, cfg)

    print()
    print("=" * 70)
    print(f"  DRE SIMULACAO — {seller_upper} — {cfg.month_year} (COMPETENCIA)")
    print(f"  Seller: {seller_slug}")
    print(f"  Periodo DRE: {cfg.start} a {cfg.end}")
    print(f"  Criterio: date_approved em BRT (regime de competencia)")
    print("=" * 70)

    # -- Verificar arquivos -------------------------------------------------------
    if not cache_file.exists():
        print(f"\nERRO: Cache nao encontrado: {cache_file}")
        print("Execute rebuild_cache.py primeiro para gerar o cache.")
        return {"error": "cache not found"}

    if not extrato_file.exists():
        print(f"\nERRO: Extrato nao encontrado: {extrato_file}")
        return {"error": "extrato not found"}

    # -- Fase 1: Carrega cache de payments ----------------------------------------
    print(f"\nCarregando payments do cache: {cache_file.name}")
    with open(cache_file) as f:
        cache_data = json.load(f)

    payments_all = cache_data["payments"]
    print(f"Total de payments no cache: {len(payments_all)}")
    counts = cache_data.get("counts", {})
    print(f"Contagens do cache: {counts}")

    # -- Filtra por periodo usando date_approved ----------------------------------
    payments_target = [
        p for p in payments_all
        if _to_brt_date(p.get("date_approved") or p.get("date_created", ""))[:7] == cfg.target_ym
    ]
    print(f"Payments com date_approved (BRT) em {cfg.short_year_lower}: {len(payments_target)}")

    payments_prev = [
        p for p in payments_all
        if _to_brt_date(p.get("date_approved") or p.get("date_created", ""))[:7] == cfg.prev_ym
    ]
    print(f"Payments com date_approved (BRT) em {cfg.prev_short_year.lower()}: {len(payments_prev)}")

    # -- Fase 2: Simula todos os payments -----------------------------------------
    print("\nSimulando processamento de payments (regime de competencia)...")

    simulated_target = [simulate_payment_for_dre(p) for p in payments_target]
    simulated_prev = [simulate_payment_for_dre(p) for p in payments_prev]
    simulated_all = [simulate_payment_for_dre(p) for p in payments_all]

    non_order_target = [s for s in simulated_target if s["action"] == "NON_ORDER"]
    simulated_prev_target_cash = [
        s for s in simulated_prev
        if s["action"] in ("APPROVED", "CHARGED_BACK_REIMBURSED")
        and s.get("money_release_date", "")[:7] == cfg.target_ym
    ]

    print(f"  Simulated target: {len(simulated_target)} payments (date_approved BRT em {cfg.month_short_lower})")
    print(f"  Non-orders target: {len(non_order_target)}")
    print(f"  {cfg.prev_short} aprovados, release {cfg.month_short_lower}: {len(simulated_prev_target_cash)}")

    # -- Fase 3: Parse do extrato -------------------------------------------------
    print(f"\nLendo extrato: {extrato_file.name}")
    extrato_summary, extrato_transactions = parse_extrato(extrato_file)
    target_transactions = [tx for tx in extrato_transactions if tx["date"][:7] == cfg.target_ym]
    print(f"Total linhas extrato: {len(extrato_transactions)}")
    print(f"Linhas de {cfg.month_name_lower}: {len(target_transactions)}")
    print(f"Extrato: creditos={fmt_brl(extrato_summary['credits'])}  "
          f"debitos={fmt_brl(extrato_summary['debits'])}")

    # -- Fase 4: Gaps do extrato (linhas nao cobertas pela API) -------------------
    print("\nClassificando gaps do extrato...")
    extrato_gaps = classify_extrato_gaps(extrato_transactions, simulated_all)
    target_gaps = [g for g in extrato_gaps if g.get("date", "")[:7] == cfg.target_ym]
    print(f"Gap lines identificadas: {len(extrato_gaps)} total, {len(target_gaps)} em {cfg.month_short_lower}")

    # -- Fase 5: Caixa do mes-alvo ------------------------------------------------
    caixa_data = compute_caixa_from_extrato(extrato_transactions, simulated_all, extrato_gaps, cfg)

    # -- Fase 6: Constroi o DRE ---------------------------------------------------
    print("\nConstruindo DRE por competencia...")
    dre_data = build_dre_from_simulated(
        simulated_target,
        simulated_prev_target_cash,
        non_order_target,
        extrato_gaps,
        cfg,
    )

    # -- IMPRESSAO DOS RESULTADOS -------------------------------------------------
    print_payments_summary(simulated_target, simulated_prev, non_order_target, cfg)
    print_dre_report(
        dre_data, extrato_summary, extrato_transactions,
        caixa_data, simulated_target, simulated_prev_target_cash,
        non_order_target, extrato_gaps,
        seller_slug=seller_slug, cfg=cfg,
    )
    print_category_breakdown(simulated_target, non_order_target, extrato_gaps, dre_data, cfg)
    print_dfc_report(extrato_transactions, extrato_summary, seller_slug, cfg)
    print_cross_month_analysis(dre_data, simulated_target, simulated_prev, cfg)

    # -- RESUMO FINAL -------------------------------------------------------------
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
    print(f"  RESUMO EXECUTIVO — DRE {seller_upper} {cfg.month_year}")
    print("=" * 70)
    print()
    print(f"  Receita bruta (1.1.1):          {fmt_brl(total_receita_bruta)}")
    print(f"  (-) Devolucoes (1.2.1):         {fmt_brl(-total_devolucao)}")
    if dre["devolucao"]["count"] > 0:
        from_proc = sum(1 for s in simulated_target if s["action"] == "REFUNDED"
                       and s.get("competencia_estorno", "")[:7] == cfg.target_ym)
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
    print(f"    {cfg.prev_short} aprovados, {cfg.month_short} caixa:     {len(cross_month['prev_approved_target_release'])} payments"
          f"  ({fmt_brl(sum(p['amount'] for p in cross_month['prev_approved_target_release']))})")
    print(f"    {cfg.month_short} aprovados, {cfg.next_short} caixa:     {len(cross_month['target_approved_next_release'])} payments"
          f"  ({fmt_brl(sum(p['amount'] for p in cross_month['target_approved_next_release']))})")
    print(f"    {cfg.month_short} aprovados, {cfg.month_short} caixa:     {len(cross_month['target_approved_target_release'])} payments"
          f"  ({fmt_brl(sum(p['amount'] for p in cross_month['target_approved_target_release']))})")
    print()
    print(f"  Extrato real {cfg.month_short_lower} (caixa):")
    print(f"    Total movimentacao:           {fmt_brl(sum(tx['amount'] for tx in target_transactions))}")
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
    parser = argparse.ArgumentParser(
        description="DRE Simulacao — Parametrico por Mes (Competencia)"
    )
    parser.add_argument("--month", type=str, default="2026-01",
                        help="Mes alvo no formato YYYY-MM (ex: 2026-01, 2026-02)")
    parser.add_argument("--seller", type=str, default=None,
                        help="Seller slug (auto-descoberto dos extratos)")
    parser.add_argument("--all", action="store_true",
                        help="Roda para todos os sellers disponiveis")
    args = parser.parse_args()

    cfg = MonthConfig(args.month)

    # Discover available sellers for this month
    available = discover_sellers(cfg)
    all_sellers = list(available.keys())

    if not all_sellers:
        print(f"ERRO: Nenhum extrato encontrado para {cfg.month_name_lower} {cfg.year}")
        print(f"Procurando por: extrato {cfg.month_name_lower} *.csv em {cfg.extratos_dir}")
        sys.exit(1)

    if args.all:
        sellers = all_sellers
    elif args.seller:
        sellers = [args.seller]
    else:
        # Default: first seller
        sellers = [all_sellers[0]]

    print(f"\nMes: {cfg.month_year}")
    print(f"Sellers disponiveis: {', '.join(all_sellers)}")
    print(f"Cache dir: {cfg.cache_dir}")
    print()

    results = {}
    for slug in sellers:
        result = run_seller_dre(slug, cfg)
        results[slug] = result

    if len(sellers) > 1:
        print("\n" + "=" * 70)
        print(f"  SUMARIO FINAL DRE — TODOS OS SELLERS — {cfg.month_year}")
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
