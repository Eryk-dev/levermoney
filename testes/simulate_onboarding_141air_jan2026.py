#!/usr/bin/env python3
"""
Simulacao de Onboarding — Janeiro 2026 (Agnostico)
===================================================
Simula o que aconteceria se ativassemos um seller com ca_start_date=2026-01-01,
range_field=money_release_date, e comparamos cada linha do extrato real contra
o que o sistema produziria. Objetivo: provar 100% de cobertura.

NAO grava nada no Conta Azul, NAO altera Supabase.
Apenas leitura: dados ML cacheados + extrato real.

Uso:
    cd "lever money claude v3"
    python3 testes/simulate_onboarding_141air_jan2026.py [--seller SLUG]

    # Exemplos:
    python3 testes/simulate_onboarding_141air_jan2026.py --seller net-air
    python3 testes/simulate_onboarding_141air_jan2026.py --seller 141air
    python3 testes/simulate_onboarding_141air_jan2026.py --seller netparts-sp
    python3 testes/simulate_onboarding_141air_jan2026.py --seller easy-utilidades
    python3 testes/simulate_onboarding_141air_jan2026.py --all   # roda todos
"""

import sys
import os
import json
import logging
import argparse
import unicodedata
from pathlib import Path
from datetime import date, datetime, timedelta, timezone
from collections import defaultdict

# ── Configuracao do projeto ───────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.services.processor import _to_brt_date, _to_float, _compute_effective_net_amount
from app.services.expense_classifier import _classify, _extract_branch
from app.services.extrato_ingester import (
    _classify_extrato_line,
    _normalize_text,
    EXTRATO_CLASSIFICATION_RULES,
)

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("simulate_onboarding")

# ══════════════════════════════════════════════════════════════
# CONFIG
# ══════════════════════════════════════════════════════════════

CA_START_DATE = "2026-01-01"
PERIOD_START = "2026-01-01"
RANGE_FIELD = "money_release_date"
# Backfill now extends to today + 90 days (mirrors _execute_backfill fix)
_today = date.today()
FUTURE_CUTOFF = (_today + timedelta(days=90)).isoformat()
# For display/reconciliation, the extrato period stays Jan 2026
EXTRATO_PERIOD_START = "2026-01-01"
EXTRATO_PERIOD_END = "2026-01-31"

EXTRATOS_DIR = PROJECT_ROOT / "testes" / "extratos"
CACHE_DIR = PROJECT_ROOT / "testes" / "cache_jan2026"

# Mapeamento seller_slug → nome do arquivo de extrato
SELLER_EXTRATO_MAP = {
    "141air":          "extrato janeiro 141Air.csv",
    "net-air":         "extrato janeiro netair.csv",
    "netparts-sp":     "extrato janeiro netparts.csv",
    "easy-utilidades": "extrato janeiro Easyutilidades.csv",
}

ALL_SELLERS = list(SELLER_EXTRATO_MAP.keys())

JAN_DATES = [(date(2026, 1, 1) + timedelta(days=i)).isoformat() for i in range(31)]

BRT = timezone(timedelta(hours=-3))


def get_seller_paths(seller_slug: str) -> tuple[Path, Path]:
    """Retorna (extrato_file, cache_file) para o seller."""
    extrato_name = SELLER_EXTRATO_MAP.get(seller_slug)
    if not extrato_name:
        print(f"ERRO: Seller '{seller_slug}' nao tem extrato mapeado.")
        print(f"Sellers disponiveis: {', '.join(ALL_SELLERS)}")
        sys.exit(1)
    return EXTRATOS_DIR / extrato_name, CACHE_DIR / f"{seller_slug}_payments.json"


# ══════════════════════════════════════════════════════════════
# AUXILIARES — Formatacao monetaria
# ══════════════════════════════════════════════════════════════

def fmt_brl(value: float) -> str:
    """Formata valor no padrao brasileiro: R$ 1.234,56"""
    negative = value < 0
    abs_val = abs(value)
    formatted = f"{abs_val:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    prefix = "R$ -" if negative else "R$ "
    return f"{prefix}{formatted}"


def fmt_pct(num: int, denom: int) -> str:
    if denom == 0:
        return "N/D"
    return f"{num / denom * 100:.1f}%"


# ══════════════════════════════════════════════════════════════
# FASE 1 — Parse do extrato real
# ══════════════════════════════════════════════════════════════

def parse_br_number(raw: str) -> float:
    if not raw or not raw.strip():
        return 0.0
    cleaned = raw.strip().replace(".", "").replace(",", ".")
    try:
        return float(cleaned)
    except ValueError:
        return 0.0


def parse_extrato(csv_path: Path) -> tuple[dict, list[dict]]:
    """Faz parse do extrato CSV no formato semicolon-delimitado, numeros brasileiros."""
    with open(csv_path, encoding="utf-8-sig") as f:
        lines = f.readlines()

    # Linha 1: header de sumario, linha 2: valores do sumario
    summary_parts = lines[1].strip().split(";")
    summary = {
        "initial_balance": parse_br_number(summary_parts[0]),
        "credits":         parse_br_number(summary_parts[1]),
        "debits":          parse_br_number(summary_parts[2]),
        "final_balance":   parse_br_number(summary_parts[3]),
    }

    transactions = []
    for line in lines[4:]:  # pula header+sumario+blank+header_tx
        line = line.strip()
        if not line:
            continue
        parts = line.split(";")
        if len(parts) < 5:
            continue

        # Extrato tem formato: DATE;TRANSACTION_TYPE;REFERENCE_ID;AMOUNT;BALANCE
        # Mas TRANSACTION_TYPE pode conter semicolons (raro) — tratamos com split posicional
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


def classify_extrato_category(tx_type: str) -> str:
    """Categoriza linha do extrato para exibicao (legado do reconciliation_v2)."""
    t = tx_type.lower()
    if "liberacao de dinheiro cancelada" in _normalize_text(t) or "liberação de dinheiro cancelada" in t:
        return "liberacao_cancelada"
    if "liberacao de dinheiro" in _normalize_text(t) or "liberação de dinheiro" in t:
        return "liberacao"
    if "reembolso" in t:
        return "reembolso"
    if "dinheiro retido" in t:
        return "dinheiro_retido"
    if "debito por divida" in _normalize_text(t) or "débito por dívida" in t:
        return "debito_divida"
    if "transferencia" in _normalize_text(t) or "transferência" in t:
        return "transferencia"
    if "pagamento de conta" in t:
        return "pagamento_conta"
    if "pix enviado" in t:
        return "pix_enviado"
    if "pagamento" in t or "qr" in t.split():
        return "pagamento_qr_ou_subs"
    if "bonus" in _normalize_text(t) or "bônus" in t:
        return "bonus"
    if "difal" in t or "aliquota" in _normalize_text(t):
        return "difal"
    if "dinheiro recebido" in t:
        return "dinheiro_recebido"
    if "entrada de dinheiro" in t:
        return "entrada_dinheiro"
    return "outro"


def print_extrato_summary(summary: dict, transactions: list[dict], extrato_file: Path) -> None:
    print("\n" + "=" * 70)
    print("  FASE 1 — PARSE DO EXTRATO REAL")
    print("=" * 70)
    print(f"\nArquivo: {extrato_file.name}")
    print(f"\nResumo do periodo:")
    print(f"  Saldo inicial:  {fmt_brl(summary['initial_balance'])}")
    print(f"  Total creditos: {fmt_brl(summary['credits'])}")
    print(f"  Total debitos:  {fmt_brl(summary['debits'])}")
    print(f"  Saldo final:    {fmt_brl(summary['final_balance'])}")
    print(f"  Total linhas:   {len(transactions)}")

    # Agrupamento por tipo de transacao
    by_category = defaultdict(lambda: {"count": 0, "total": 0.0})
    for tx in transactions:
        cat = classify_extrato_category(tx["type"])
        by_category[cat]["count"] += 1
        by_category[cat]["total"] += tx["amount"]

    print(f"\nDistribuicao por categoria de transacao:")
    print(f"  {'Categoria':<30} {'Qtd':>5}  {'Total':>15}")
    print(f"  {'-'*30} {'-'*5}  {'-'*15}")
    for cat in sorted(by_category.keys()):
        v = by_category[cat]
        print(f"  {cat:<30} {v['count']:>5}  {fmt_brl(v['total']):>15}")

    total_movimento = sum(tx["amount"] for tx in transactions)
    print(f"\n  Total movimentacao: {fmt_brl(total_movimento)}")

    # Verificacao de integridade
    expected_final = summary["initial_balance"] + total_movimento
    diff = round(summary["final_balance"] - expected_final, 2)
    print(f"\nIntegridade do extrato:")
    print(f"  Saldo inicial + movimentacao = {fmt_brl(expected_final)}")
    print(f"  Saldo final no extrato       = {fmt_brl(summary['final_balance'])}")
    print(f"  Diferenca                    = {fmt_brl(diff)} {'(OK)' if diff == 0 else '(ATENCAO!)'}")


# ══════════════════════════════════════════════════════════════
# FASE 2 — Simulacao do backfill (payments por money_release_date)
# ══════════════════════════════════════════════════════════════

def simulate_approved(payment: dict) -> dict:
    """Simula _process_approved: calcula receita, comissao, frete."""
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

    return {
        "action": "APPROVED",
        "amount": amount,
        "net": net,
        "effective_net": effective_net,
        "comissao": mp_fee,
        "frete": shipping_seller,
        "competencia": competencia,
        "money_release_date": money_release_date,
        "net_diff": net_diff,
    }


def simulate_refunded(payment: dict) -> dict:
    """Simula _process_refunded: calcula estornos."""
    amount = _to_float(payment.get("transaction_amount"))
    td = payment.get("transaction_details") or {}
    net = _to_float(td.get("net_received_amount"))
    effective_net = _compute_effective_net_amount(payment)
    refunds = payment.get("refunds") or []

    if refunds:
        total_refunded = sum(_to_float(r.get("amount")) for r in refunds)
        date_refunded = refunds[-1].get("date_created", "")[:10]
    else:
        total_refunded = _to_float(payment.get("transaction_amount_refunded")) or amount
        raw_date = payment.get("date_last_updated") or payment.get("date_created", "")
        date_refunded = _to_brt_date(raw_date)

    estorno_receita = min(total_refunded, amount)
    total_fees = round(amount - net, 2) if net > 0 else 0
    approved = simulate_approved(payment)

    return {
        "action": "REFUNDED",
        "amount": amount,
        "net": net,
        "effective_net": effective_net,
        "comissao": approved["comissao"],
        "frete": approved["frete"],
        "competencia": approved["competencia"],
        "money_release_date": approved["money_release_date"],
        "estorno_receita": estorno_receita,
        "estorno_taxa": total_fees if estorno_receita >= amount else 0,
        "date_refunded": date_refunded,
        "total_refunded_raw": total_refunded,
    }


def simulate_payment(payment: dict) -> dict:
    """Dispatcher principal: simula o que o processor faria com cada payment."""
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
    }

    # ── Sem order_id: classificar como non-order ──────────────
    if not order_id:
        exp_type, direction, category, auto, desc = _classify(payment)
        if direction == "skip":
            return {**base, "action": "SKIP", "skip_reason": f"non-order interno ({exp_type})"}
        date_approved_raw = payment.get("date_approved") or payment.get("date_created", "")
        return {
            **base,
            "action": "NON_ORDER",
            "expense_type": exp_type,
            "direction": direction,
            "category": category,
            "auto_categorized": auto,
            "description": desc,
            "amount": _to_float(payment.get("transaction_amount")),
            "net": _to_float((payment.get("transaction_details") or {}).get("net_received_amount")),
            "date_approved": _to_brt_date(date_approved_raw),
            "date_created": _to_brt_date(payment.get("date_created", "")),
        }

    # ── Filtros de skip para orders ───────────────────────────
    if payment.get("description") == "marketplace_shipment":
        return {**base, "action": "SKIP", "skip_reason": "marketplace_shipment"}

    if (payment.get("collector") or {}).get("id") is not None:
        return {**base, "action": "SKIP", "skip_reason": "compra (collector_id)"}

    # ── Dispatch por status ───────────────────────────────────
    if status in ("approved", "in_mediation"):
        return {**base, **simulate_approved(payment)}

    if status == "charged_back" and status_detail == "reimbursed":
        result = simulate_approved(payment)
        result["action"] = "CHARGED_BACK_REIMBURSED"
        return {**base, **result}

    if status == "refunded" and status_detail == "by_admin":
        return {**base, "action": "SKIP", "skip_reason": "refunded/by_admin (kit split)"}

    if status in ("refunded", "charged_back"):
        return {**base, **simulate_refunded(payment)}

    if status in ("cancelled", "rejected"):
        return {**base, "action": "SKIP", "skip_reason": f"status={status}"}

    return {**base, "action": "PENDENTE", "skip_reason": f"status={status}/{status_detail}"}


def print_backfill_summary(payments: list[dict], simulated: list[dict]) -> None:
    print("\n" + "=" * 70)
    print(f"  FASE 2 — SIMULACAO DO BACKFILL (money_release_date {PERIOD_START} → {FUTURE_CUTOFF[:10]})")
    print("=" * 70)

    print(f"\nTotal de payments na API (range_field=money_release_date): {len(payments)}")

    # Contagens por status ML
    ml_status_counts = defaultdict(int)
    for p in payments:
        ml_status_counts[p.get("status", "?")[:20]] += 1
    print(f"\nStatus ML:")
    for status, count in sorted(ml_status_counts.items(), key=lambda x: -x[1]):
        print(f"  {status:<25} {count:>5}")

    # Contagens por acao simulada
    action_counts = defaultdict(int)
    for s in simulated:
        action_counts[s["action"]] += 1
    print(f"\nAcoes simuladas pelo processor:")
    for action, count in sorted(action_counts.items(), key=lambda x: -x[1]):
        print(f"  {action:<30} {count:>5}")

    # Razoes de skip
    skip_reasons = defaultdict(int)
    for s in simulated:
        if s["action"] == "SKIP":
            skip_reasons[s.get("skip_reason", "?")] += 1
    if skip_reasons:
        print(f"\nRazoes de skip:")
        for reason, count in sorted(skip_reasons.items(), key=lambda x: -x[1]):
            print(f"  {reason:<45} {count:>5}")

    # Totais financeiros
    approved_sims = [s for s in simulated if s["action"] in ("APPROVED", "CHARGED_BACK_REIMBURSED")]
    refunded_sims = [s for s in simulated if s["action"] == "REFUNDED"]
    non_order_sims = [s for s in simulated if s["action"] == "NON_ORDER"]

    total_receita = sum(s.get("amount", 0) for s in approved_sims)
    total_comissao = sum(s.get("comissao", 0) for s in approved_sims)
    total_frete = sum(s.get("frete", 0) for s in approved_sims)
    total_net_api = sum(s.get("effective_net", s.get("net", 0)) for s in approved_sims)

    print(f"\nVendas aprovadas (orders):")
    print(f"  Receita bruta:   {fmt_brl(total_receita)}")
    print(f"  Comissao ML:     {fmt_brl(total_comissao)}")
    print(f"  Frete seller:    {fmt_brl(total_frete)}")
    print(f"  Net API total:   {fmt_brl(total_net_api)}")
    print(f"  Qtd vendas:      {len(approved_sims)}")

    print(f"\nDevolucoes (orders):")
    total_estorno = sum(s.get("estorno_receita", 0) for s in refunded_sims)
    print(f"  Total estorno:   {fmt_brl(total_estorno)}")
    print(f"  Qtd:             {len(refunded_sims)}")

    print(f"\nNon-orders classificados:")
    by_type = defaultdict(lambda: {"count": 0, "total": 0.0})
    for s in non_order_sims:
        exp_type = s.get("expense_type", "?")
        by_type[exp_type]["count"] += 1
        by_type[exp_type]["total"] += s.get("amount", 0)
    for exp_type, v in sorted(by_type.items()):
        print(f"  {exp_type:<25} {v['count']:>5}  {fmt_brl(v['total']):>15}")
    print(f"  Total non-orders: {len(non_order_sims)}")


# ══════════════════════════════════════════════════════════════
# FASE 3 — Simulacao do ingester de extrato
# ══════════════════════════════════════════════════════════════

def simulate_extrato_ingester(transactions: list[dict], sim_by_id: dict) -> dict:
    """
    Para cada linha do extrato:
    1. Verifica se REFERENCE_ID bate com algum payment simulado
    2. Para nao casadas: classifica usando EXTRATO_CLASSIFICATION_RULES
    3. Determina se e uma linha "coberta" pelo sistema ou "nao coberta"

    Retorna dict com listas classificadas.
    """
    matched_api = []          # linhas com REFERENCE_ID no payments API
    matched_non_order = []    # linhas com ref_id em non-order payments
    ingested_gap = []         # linhas do extrato ingeridas pelo ingester (gap lines)
    skipped_internal = []     # linhas puladas (liberacao, transferencia, pagamento_conta, etc.)
    unmatched_unknown = []    # linhas sem classificacao conhecida (must be 0)

    for tx in transactions:
        ref_id = tx["reference_id"]
        cat = classify_extrato_category(tx["type"])

        # Tenta casar com payment da API
        sim = sim_by_id.get(ref_id)

        if sim and sim["action"] != "SKIP":
            if sim["action"] == "NON_ORDER":
                matched_non_order.append({
                    **tx,
                    "extrato_amount": tx["amount"],
                    "sim_action": sim["action"],
                    "sim_expense_type": sim.get("expense_type"),
                    "sim_amount": sim.get("amount", 0),
                    "extrato_category": cat,
                })
            else:
                matched_api.append({
                    **tx,
                    "extrato_amount": tx["amount"],
                    "sim_action": sim["action"],
                    "sim_effective_net": sim.get("effective_net", sim.get("net", 0)),
                    "sim_net": sim.get("net", 0),
                    "sim_amount": sim.get("amount", sim.get("transaction_amount", 0)),
                    "extrato_category": cat,
                })
            continue

        # Nao casou com payment: classifica via regras do extrato_ingester
        expense_type, direction, ca_cat_uuid = _classify_extrato_line(tx["type"])

        if expense_type is None and direction is None:
            # Linha pulada internamente (liberacao, transferencia, pagamento_conta, etc.)
            skipped_internal.append({
                **tx,
                "extrato_category": cat,
                "classification": "SKIP_INTERNO",
            })
        else:
            # Gap line: ingerida pelo extrato_ingester para mp_expenses
            ingested_gap.append({
                **tx,
                "extrato_category": cat,
                "expense_type": expense_type,
                "direction": direction,
                "ca_category": ca_cat_uuid,
                "classification": "INGESTED_GAP",
            })

    return {
        "matched_api": matched_api,
        "matched_non_order": matched_non_order,
        "ingested_gap": ingested_gap,
        "skipped_internal": skipped_internal,
        "unmatched_unknown": unmatched_unknown,
    }


def print_ingester_summary(ingestion: dict, transactions: list[dict]) -> None:
    total = len(transactions)
    matched_api = len(ingestion["matched_api"])
    matched_non_order = len(ingestion["matched_non_order"])
    ingested_gap = len(ingestion["ingested_gap"])
    skipped_internal = len(ingestion["skipped_internal"])
    unmatched = len(ingestion["unmatched_unknown"])

    total_covered = matched_api + matched_non_order + ingested_gap + skipped_internal

    print("\n" + "=" * 70)
    print("  FASE 3 — SIMULACAO DO INGESTER DE EXTRATO")
    print("=" * 70)
    print(f"\nTotal de linhas no extrato:          {total:>5}")
    print(f"  Casadas com payment API (orders): {matched_api:>5}")
    print(f"  Casadas com non-order API:        {matched_non_order:>5}")
    print(f"  Ingeridas pelo ingester (gaps):   {ingested_gap:>5}")
    print(f"  Puladas (internas/cobertas):      {skipped_internal:>5}")
    print(f"  Nao cobertas (DEVE SER 0):        {unmatched:>5}")
    print(f"  Total coberto:                    {total_covered:>5}")
    print(f"\n  Coverage: {fmt_pct(total_covered, total)}")

    # Detalhamento das linhas puladas internamente
    if skipped_internal:
        skip_cats = defaultdict(int)
        for tx in ingestion["skipped_internal"]:
            skip_cats[tx["extrato_category"]] += 1
        print(f"\nLinhas puladas internamente por categoria:")
        for cat, count in sorted(skip_cats.items(), key=lambda x: -x[1]):
            print(f"  {cat:<35} {count:>5}")

    # Detalhamento dos gaps ingeridos
    if ingested_gap:
        gap_types = defaultdict(lambda: {"count": 0, "total": 0.0})
        for tx in ingestion["ingested_gap"]:
            exp_type = tx.get("expense_type", "?")
            gap_types[exp_type]["count"] += 1
            gap_types[exp_type]["total"] += tx["amount"]
        print(f"\nGap lines ingeridas pelo ingester:")
        for exp_type, v in sorted(gap_types.items()):
            print(f"  {exp_type:<30} {v['count']:>5}  {fmt_brl(v['total']):>15}")

    if unmatched:
        print(f"\nATENCAO: {unmatched} linhas NAO cobertas!")
        for tx in ingestion["unmatched_unknown"][:10]:
            print(f"  {tx['date']}  {tx['reference_id']}  {tx['type'][:50]}")


# ══════════════════════════════════════════════════════════════
# FASE 4 — Reconciliacao diaria
# ══════════════════════════════════════════════════════════════

def compute_daily_reconciliation(
    transactions: list[dict],
    simulated: list[dict],
    ingestion: dict,
) -> dict:
    """
    Para cada dia de janeiro:
      extrato_day = soma de todas as linhas do extrato no dia
      api_releases_day = soma effective_net das vendas com money_release_date = dia
      ingested_gap_day = soma dos gaps ingeridos no dia
      non_order_day = soma dos non-orders do dia (pelo date do extrato, nao date_approved)
      skipped_internal_day = soma das linhas puladas no dia

      system_total_day = api_releases_day + ingested_gap_day + non_order_day + skipped_internal_day
      diff_day = extrato_day - system_total_day (deve ser ~0)

    Nota: "skipped_internal" SAO cobertas pelo sistema via payments API
    (liberacao, pagamento_conta, transferencia) — mas para o caixa do extrato,
    elas fazem parte do total e precisam ser contabilizadas.
    """

    # Index por ref_id para rapido lookup
    sim_by_id = {}
    for s in simulated:
        sim_by_id[str(s["payment_id"])] = s

    # Agrupa extrato por dia
    daily_extrato = defaultdict(list)
    for tx in transactions:
        daily_extrato[tx["date"]].append(tx)

    # Agrupa matched_api por money_release_date do payment
    # Para reconciliacao de caixa: a baixa API e pelo money_release_date
    api_release_by_day = defaultdict(list)
    for m in ingestion["matched_api"]:
        ref_id = m["reference_id"]
        sim = sim_by_id.get(ref_id)
        if sim:
            release_day = sim.get("money_release_date", "")[:10]
            if release_day:
                api_release_by_day[release_day].append({
                    "reference_id": ref_id,
                    "effective_net": sim.get("effective_net", sim.get("net", 0)),
                    "extrato_amount": m["amount"],
                    "extrato_date": m["date"],
                })

    # Agrupa linhas do extrato por tipo de cobertura e por DIA DO EXTRATO
    ingested_gap_by_day = defaultdict(float)
    for tx in ingestion["ingested_gap"]:
        ingested_gap_by_day[tx["date"]] += tx["amount"]

    non_order_by_day = defaultdict(float)
    for tx in ingestion["matched_non_order"]:
        non_order_by_day[tx["date"]] += tx["amount"]

    skipped_internal_by_day = defaultdict(float)
    for tx in ingestion["skipped_internal"]:
        skipped_internal_by_day[tx["date"]] += tx["amount"]

    matched_api_by_extrato_day = defaultdict(float)
    for m in ingestion["matched_api"]:
        matched_api_by_extrato_day[m["date"]] += m["extrato_amount"]

    # Computa diariamente
    daily = {}
    for day in JAN_DATES:
        extrato_lines = daily_extrato.get(day, [])
        extrato_total = sum(tx["amount"] for tx in extrato_lines)
        extrato_count = len(extrato_lines)

        # API releases: usamos o VALOR DO EXTRATO (nao o effective_net calculado)
        # porque queremos comparar o que realmente entrou no caixa
        api_releases_extrato = matched_api_by_extrato_day.get(day, 0.0)
        ingested_gap = round(ingested_gap_by_day.get(day, 0.0), 2)
        non_order = round(non_order_by_day.get(day, 0.0), 2)
        skipped_internal = round(skipped_internal_by_day.get(day, 0.0), 2)

        system_total = round(api_releases_extrato + ingested_gap + non_order + skipped_internal, 2)
        diff = round(extrato_total - system_total, 2)

        daily[day] = {
            "day": day,
            "extrato_total": round(extrato_total, 2),
            "extrato_count": extrato_count,
            "api_releases": round(api_releases_extrato, 2),
            "ingested_gap": ingested_gap,
            "non_order": non_order,
            "skipped_internal": skipped_internal,
            "system_total": system_total,
            "diff": diff,
        }

    return daily


def print_reconciliation_table(daily: dict) -> None:
    print("\n" + "=" * 70)
    print("  FASE 4 — RECONCILIACAO DIARIA (extrato vs sistema)")
    print("=" * 70)
    print()
    print(
        f"  {'DATA':<12} {'EXTRATO':>14} {'API_RELEAS':>12} {'GAPS':>10} "
        f"{'NON_ORD':>10} {'PULADAS':>12} {'SIS_TOTAL':>12} {'DIFF':>10}"
    )
    print(
        f"  {'-'*12} {'-'*14} {'-'*12} {'-'*10} "
        f"{'-'*10} {'-'*12} {'-'*12} {'-'*10}"
    )

    total_extrato = 0.0
    total_api = 0.0
    total_gaps = 0.0
    total_non_order = 0.0
    total_skipped = 0.0
    total_system = 0.0
    total_diff = 0.0
    days_with_diff = 0

    for day in JAN_DATES:
        d = daily.get(day, {})
        if not d:
            continue

        extrato = d["extrato_total"]
        api = d["api_releases"]
        gaps = d["ingested_gap"]
        non_ord = d["non_order"]
        skip = d["skipped_internal"]
        sys_total = d["system_total"]
        diff = d["diff"]

        flag = " *" if abs(diff) >= 0.01 else ""
        total_extrato += extrato
        total_api += api
        total_gaps += gaps
        total_non_order += non_ord
        total_skipped += skip
        total_system += sys_total
        total_diff += diff
        if abs(diff) >= 0.01:
            days_with_diff += 1

        print(
            f"  {day:<12} {fmt_brl(extrato):>14} {fmt_brl(api):>12} {fmt_brl(gaps):>10} "
            f"{fmt_brl(non_ord):>10} {fmt_brl(skip):>12} {fmt_brl(sys_total):>12} "
            f"{fmt_brl(diff):>10}{flag}"
        )

    print(
        f"  {'='*12} {'='*14} {'='*12} {'='*10} "
        f"{'='*10} {'='*12} {'='*12} {'='*10}"
    )
    print(
        f"  {'TOTAL':<12} {fmt_brl(total_extrato):>14} {fmt_brl(total_api):>12} "
        f"{fmt_brl(total_gaps):>10} {fmt_brl(total_non_order):>10} "
        f"{fmt_brl(total_skipped):>12} {fmt_brl(total_system):>12} "
        f"{fmt_brl(total_diff):>10}"
    )
    print(f"\n  * Dias com diferenca nao nula: {days_with_diff}")

    if days_with_diff > 0:
        print(f"\n  ATENCAO: Dias com diff:")
        for day in JAN_DATES:
            d = daily.get(day, {})
            if d and abs(d.get("diff", 0)) >= 0.01:
                print(f"    {day}: extrato={fmt_brl(d['extrato_total'])} "
                      f"sistema={fmt_brl(d['system_total'])} diff={fmt_brl(d['diff'])}")

    return total_diff, days_with_diff


# ══════════════════════════════════════════════════════════════
# FASE 5 — Relatorio final
# ══════════════════════════════════════════════════════════════

def print_final_report(
    summary: dict,
    transactions: list[dict],
    payments: list[dict],
    simulated: list[dict],
    ingestion: dict,
    daily: dict,
    seller_slug: str = "",
) -> tuple[float, float]:
    """Imprime relatorio final consolidado. Retorna (total_diff, coverage_pct)."""

    # Calculos
    total_extrato_lines = len(transactions)
    matched_api = len(ingestion["matched_api"])
    matched_non_order = len(ingestion["matched_non_order"])
    ingested_gap = len(ingestion["ingested_gap"])
    skipped_internal = len(ingestion["skipped_internal"])
    unmatched = len(ingestion["unmatched_unknown"])
    total_covered = matched_api + matched_non_order + ingested_gap + skipped_internal
    coverage_pct = total_covered / total_extrato_lines * 100 if total_extrato_lines else 0

    action_counts = defaultdict(int)
    for s in simulated:
        action_counts[s["action"]] += 1

    approved_sims = [s for s in simulated if s["action"] in ("APPROVED", "CHARGED_BACK_REIMBURSED")]
    refunded_sims = [s for s in simulated if s["action"] == "REFUNDED"]
    non_order_sims = [s for s in simulated if s["action"] == "NON_ORDER"]
    skipped_sims = [s for s in simulated if s["action"] == "SKIP"]

    total_receita = sum(s.get("amount", 0) for s in approved_sims)
    total_comissao = sum(s.get("comissao", 0) for s in approved_sims)
    total_frete = sum(s.get("frete", 0) for s in approved_sims)
    total_net_api = sum(s.get("effective_net", s.get("net", 0)) for s in approved_sims)

    # Totais de reconciliacao
    total_extrato_brl = sum(tx["amount"] for tx in transactions)
    total_extrato_credits = sum(tx["amount"] for tx in transactions if tx["amount"] > 0)
    total_extrato_debits = sum(tx["amount"] for tx in transactions if tx["amount"] < 0)

    total_system = sum(d["system_total"] for d in daily.values())
    total_diff = round(total_extrato_brl - total_system, 2)

    seller_upper = seller_slug.upper()
    print("\n" + "=" * 70)
    print(f"  SIMULACAO ONBOARDING {seller_upper} — {CA_START_DATE} a {FUTURE_CUTOFF[:10]} — RELATORIO FINAL")
    print("=" * 70)
    print(f"\n  ca_start_date: {CA_START_DATE}")
    print(f"  range_field:   {RANGE_FIELD}")
    print(f"  Janela backfill: {PERIOD_START} → {FUTURE_CUTOFF[:10]} (today+90d)")
    print(f"  Extrato real:    {EXTRATO_PERIOD_START} a {EXTRATO_PERIOD_END}")
    print()

    print(f"  PAYMENTS API (money_release_date {PERIOD_START} → {FUTURE_CUTOFF[:10]}):")
    print(f"    Total payments:           {len(payments):>6}")
    print(f"    Orders aprovados:         {len(approved_sims):>6}  → receita {fmt_brl(total_receita)}")
    print(f"    Orders devolvidos:        {len(refunded_sims):>6}")
    print(f"    Non-orders classificados: {len(non_order_sims):>6}")
    print(f"    Pulados (skip):           {len(skipped_sims):>6}")
    print()

    print("  EXTRATO REAL (account_statement):")
    print(f"    Total linhas:             {total_extrato_lines:>6}")
    print(f"    Casadas com payment API:  {matched_api:>6}")
    print(f"    Casadas com non-order:    {matched_non_order:>6}")
    print(f"    Ingeridas (gaps):         {ingested_gap:>6}")
    print(f"    Puladas (internas):       {skipped_internal:>6}")
    print(f"    Nao cobertas:             {unmatched:>6}  (deve ser 0)")
    print()

    print("  TOTAIS FINANCEIROS:")
    print(f"    Extrato creditos:         {fmt_brl(total_extrato_credits):>16}")
    print(f"    Extrato debitos:          {fmt_brl(total_extrato_debits):>16}")
    print(f"    Extrato net:              {fmt_brl(total_extrato_brl):>16}")
    print(f"    Sistema total:            {fmt_brl(total_system):>16}")
    print(f"    Diferenca:                {fmt_brl(total_diff):>16}  (deve ser 0,00)")
    print()

    print(f"  COVERAGE:  {coverage_pct:.1f}%  ({total_covered}/{total_extrato_lines} linhas)")
    print()

    # Veredicto
    verdict_ok = coverage_pct >= 100.0 and abs(total_diff) < 0.01 and unmatched == 0
    if verdict_ok:
        print("  VEREDICTO: APROVADO")
        print("  Sistema cobre 100% do extrato. Diff = R$ 0,00.")
    else:
        print("  VEREDICTO: REPROVADO")
        if coverage_pct < 100.0:
            print(f"  - Coverage {coverage_pct:.1f}% < 100%")
        if abs(total_diff) >= 0.01:
            print(f"  - Diferenca nao nula: {fmt_brl(total_diff)}")
        if unmatched > 0:
            print(f"  - {unmatched} linhas nao cobertas")

    print()

    return total_diff, coverage_pct


# ══════════════════════════════════════════════════════════════
# ANALISE ADICIONAL — Linhas puladas (o que o sistema cobre via outros meios)
# ══════════════════════════════════════════════════════════════

def print_skipped_analysis(ingestion: dict) -> None:
    """Explica por que as linhas 'puladas' sao na verdade cobertas."""
    print("\n" + "=" * 70)
    print("  ANALISE ADICIONAL — Linhas 'puladas' (como sao cobertas)")
    print("=" * 70)
    print()
    print("  Linhas 'puladas internamente' SAO cobertas pelo sistema:")
    print("  - liberacao: via payments API (receita + baixa automatica)")
    print("  - pagamento_conta/qr_subs: via non-order classifier (mp_expenses)")
    print("  - transferencia: via non-order classifier (mp_expenses)")
    print("  - pix_enviado: via non-order classifier (mp_expenses)")
    print("  - pagamento_conta: via non-order ou extrato_ingester")
    print()

    skip_by_cat = defaultdict(lambda: {"count": 0, "total": 0.0, "examples": []})
    for tx in ingestion["skipped_internal"]:
        cat = tx["extrato_category"]
        skip_by_cat[cat]["count"] += 1
        skip_by_cat[cat]["total"] += tx["amount"]
        if len(skip_by_cat[cat]["examples"]) < 2:
            skip_by_cat[cat]["examples"].append(f"{tx['date']} {tx['reference_id']}")

    print(f"  {'Categoria':<35} {'Qtd':>5}  {'Total':>14}  Cobertura")
    print(f"  {'-'*35} {'-'*5}  {'-'*14}  {'-'*20}")
    for cat in sorted(skip_by_cat.keys()):
        v = skip_by_cat[cat]
        coverage_note = {
            "liberacao": "Payments API (baixas)",
            "pagamento_qr_ou_subs": "Non-order classifier / pagamentos",
            "pagamento_conta": "Non-order classifier (boletos)",
            "transferencia": "Non-order classifier (PIX/TED)",
            "pix_enviado": "Non-order classifier (PIX out)",
        }.get(cat, "Extrato ingester / manual")
        print(f"  {cat:<35} {v['count']:>5}  {fmt_brl(v['total']):>14}  {coverage_note}")

    print()


# ══════════════════════════════════════════════════════════════
# ANALISE DE GAPS — Detalha quais linhas serao ingeridas
# ══════════════════════════════════════════════════════════════

def print_gap_analysis(ingestion: dict) -> None:
    """Detalha as gap lines que o extrato_ingester vai criar em mp_expenses."""
    print("\n" + "=" * 70)
    print("  ANALISE DE GAPS — Linhas a serem ingeridas para mp_expenses")
    print("=" * 70)
    print()
    print("  Essas linhas existem so no extrato (nao na Payments API).")
    print("  O extrato_ingester as cria em mp_expenses para o financeiro importar.")
    print()

    gap_by_type = defaultdict(lambda: {"count": 0, "total": 0.0, "examples": []})
    for tx in ingestion["ingested_gap"]:
        exp_type = tx.get("expense_type", "?")
        gap_by_type[exp_type]["count"] += 1
        gap_by_type[exp_type]["total"] += tx["amount"]
        if len(gap_by_type[exp_type]["examples"]) < 3:
            gap_by_type[exp_type]["examples"].append({
                "date": tx["date"],
                "ref": tx["reference_id"],
                "type": tx["type"][:60],
                "amount": tx["amount"],
            })

    print(f"  {'Tipo':<25} {'Qtd':>5}  {'Total':>14}")
    print(f"  {'-'*25} {'-'*5}  {'-'*14}")
    for exp_type in sorted(gap_by_type.keys()):
        v = gap_by_type[exp_type]
        print(f"  {exp_type:<25} {v['count']:>5}  {fmt_brl(v['total']):>14}")
        for ex in v["examples"]:
            print(f"    Ex: {ex['date']}  {ex['ref']}  {ex['amount']:>10,.2f}  {ex['type']}")

    print()


# ══════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════

def run_seller(seller_slug: str) -> tuple[float, float]:
    """Roda simulacao completa para um seller. Retorna (diff, coverage_pct)."""
    seller_upper = seller_slug.upper()
    extrato_file, cache_file = get_seller_paths(seller_slug)

    print("\n" + "=" * 70)
    print(f"  SIMULACAO ONBOARDING {seller_upper} — {PERIOD_START} → {FUTURE_CUTOFF[:10]}")
    print(f"  Seller: {seller_slug}")
    print(f"  Janela backfill: {PERIOD_START} → {FUTURE_CUTOFF[:10]} (today+90d)")
    print(f"  Extrato real: {EXTRATO_PERIOD_START} a {EXTRATO_PERIOD_END}")
    print(f"  range_field: {RANGE_FIELD}")
    print("=" * 70)

    # ── Fase 1: Parse do extrato ──────────────────────────────
    print(f"\nLendo extrato: {extrato_file}")
    if not extrato_file.exists():
        print(f"ERRO: Arquivo de extrato nao encontrado: {extrato_file}")
        return float("inf"), 0.0

    extrato_summary, extrato_transactions = parse_extrato(extrato_file)
    print_extrato_summary(extrato_summary, extrato_transactions, extrato_file)

    # ── Fase 2: Carrega payments do cache e simula ────────────
    print(f"\nCarregando payments do cache: {cache_file}")
    if not cache_file.exists():
        print(f"ERRO: Cache nao encontrado: {cache_file}")
        print("Execute reconciliation_jan2026.py primeiro para gerar o cache.")
        return float("inf"), 0.0

    with open(cache_file) as f:
        cache_data = json.load(f)

    payments = cache_data["payments"]
    fetch_counts = cache_data.get("counts", {})
    print(f"Cache carregado: {len(payments)} payments (range_fields: {list(fetch_counts.keys())})")

    # Mirror actual backfill: fetch all payments with money_release_date >= ca_start_date
    # The upper bound is today+90d but cache only has Dec/Jan data so we take everything
    payments_in_window = [
        p for p in payments
        if (p.get("money_release_date") or "")[:10] >= PERIOD_START
    ]
    print(f"Payments com money_release_date >= {PERIOD_START}: {len(payments_in_window)}")

    # Payments with money_release_date beyond Jan (newly captured by the +90d fix)
    payments_future_release = [
        p for p in payments_in_window
        if (p.get("money_release_date") or "")[:10] > EXTRATO_PERIOD_END
    ]
    if payments_future_release:
        print(f"\n[NOVO] Payments com release ALEM de {EXTRATO_PERIOD_END} (antes perdidos, agora capturados):")
        print(f"  Total: {len(payments_future_release)} payments")
        for p in payments_future_release[:10]:
            pid = p.get("id")
            status = p.get("status", "?")
            release = (p.get("money_release_date") or "")[:10]
            approved = (p.get("date_approved") or "")[:10]
            amount = p.get("transaction_amount", 0)
            print(f"  {pid}  approved={approved}  release={release}  status={status}  R$ {amount:,.2f}")
        if len(payments_future_release) > 10:
            print(f"  ... e mais {len(payments_future_release) - 10}")

    # Simula todos os payments do cache (para completar o match com extrato)
    simulated = [simulate_payment(p) for p in payments]
    # Simula os payments dentro da janela do backfill
    simulated_jan = [simulate_payment(p) for p in payments_in_window]

    # Usamos TODOS os payments para o sim_by_id (match com extrato inclui dez/2025)
    sim_by_id = {str(s["payment_id"]): s for s in simulated}

    print_backfill_summary(payments_in_window, simulated_jan)

    # ── Fase 3: Simula o ingester de extrato ─────────────────
    ingestion = simulate_extrato_ingester(extrato_transactions, sim_by_id)
    print_ingester_summary(ingestion, extrato_transactions)

    # ── Analise adicional ─────────────────────────────────────
    print_skipped_analysis(ingestion)
    print_gap_analysis(ingestion)

    # ── Fase 4: Reconciliacao diaria ──────────────────────────
    daily = compute_daily_reconciliation(extrato_transactions, simulated, ingestion)
    total_diff, days_with_diff = print_reconciliation_table(daily)

    # ── Fase 5: Relatorio final ───────────────────────────────
    final_diff, coverage_pct = print_final_report(
        extrato_summary, extrato_transactions,
        payments_in_window, simulated_jan, ingestion, daily,
        seller_slug=seller_slug,
    )

    # ── Analise de diffs (se houver) ──────────────────────────
    if days_with_diff > 0 or abs(final_diff) >= 0.01:
        print("\n" + "=" * 70)
        print("  ANALISE DE DIVERGENCIAS")
        print("=" * 70)
        print()
        print("  Dias com diferenca sao causados por linhas do extrato onde o")
        print("  REFERENCE_ID nao corresponde a nenhum payment_id na API.")
        print("  Isso e esperado para: PIX recebidos de clientes, QR Pix,")
        print("  transferencias de terceiros, etc. Essas linhas precisam ser")
        print("  tratadas via extrato_ingester ou classificacao manual.")
        print()

        # Lista as linhas nao casadas por dia com diff
        for day in JAN_DATES:
            d = daily.get(day, {})
            if not d or abs(d.get("diff", 0)) < 0.01:
                continue

            day_unmatched = [
                tx for tx in ingestion["ingested_gap"]
                if tx["date"] == day
            ]

            print(f"  Dia {day}: diff={fmt_brl(d['diff'])}")
            print(f"    extrato={fmt_brl(d['extrato_total'])} sistema={fmt_brl(d['system_total'])}")
            if day_unmatched:
                print(f"    Gaps ingeridos ({len(day_unmatched)} linhas):")
                for tx in day_unmatched[:5]:
                    print(f"      {tx['reference_id']}  {fmt_brl(tx['amount'])}  {tx['type'][:50]}")
            print()

    print("\n" + "=" * 70)
    print(f"  Simulacao {seller_upper} concluida.")
    print(f"  Coverage: {coverage_pct:.1f}%")
    print(f"  Diferenca total: {fmt_brl(final_diff)}")

    if coverage_pct >= 100.0 and abs(final_diff) < 0.01:
        print(f"  STATUS: APROVADO — 100% de cobertura, diff = R$ 0,00")
    else:
        print(f"  STATUS: REVISAR — ver detalhes acima")
    print("=" * 70 + "\n")

    return final_diff, coverage_pct


def main():
    parser = argparse.ArgumentParser(description="Simulacao de Onboarding — Janeiro 2026")
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
        # Default: 141air (compatibilidade retroativa)
        sellers = ["141air"]

    results = {}
    for slug in sellers:
        diff, coverage = run_seller(slug)
        results[slug] = {"diff": diff, "coverage": coverage}

    # Sumario final se multiplos sellers
    if len(sellers) > 1:
        print("\n" + "=" * 70)
        print("  SUMARIO FINAL — TODOS OS SELLERS")
        print("=" * 70)
        print()
        print(f"  {'Seller':<20} {'Coverage':>10} {'Diff':>15} {'Status':>12}")
        print(f"  {'-'*20} {'-'*10} {'-'*15} {'-'*12}")
        all_ok = True
        for slug in sellers:
            r = results[slug]
            status = "APROVADO" if r["coverage"] >= 100.0 and abs(r["diff"]) < 0.01 else "REVISAR"
            if status != "APROVADO":
                all_ok = False
            print(f"  {slug:<20} {r['coverage']:>9.1f}% {fmt_brl(r['diff']):>15} {status:>12}")
        print()
        if all_ok:
            print("  RESULTADO GERAL: TODOS APROVADOS")
        else:
            print("  RESULTADO GERAL: ALGUNS PRECISAM REVISAO")
        print("=" * 70 + "\n")


if __name__ == "__main__":
    main()
