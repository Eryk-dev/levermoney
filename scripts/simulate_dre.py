#!/usr/bin/env python3
"""Simulate DRE (Demonstrativo de Resultado do Exercício) for a seller/period.

Builds the income statement that *would* be lançado in Conta Azul, by
aggregating:
  - payment_events (sale_approved, fee_charged, shipping_charged, refund_*)
  - mp_expenses (uncovered extrato lines: bonus, faturas, disputas, etc.)

Maps each event/expense to its CA_CATEGORIES entry and groups by DRE block:
  RECEITAS       — 1.x
  DEDUÇÕES       — 1.2.x, 1.3.x
  CUSTOS         — 2.x.x
  DESPESAS OPER. — 2.x.x

Usage:
    python3 scripts/simulate_dre.py 141air 2026-01
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.db.supabase import get_db
from app.models.sellers import CA_CATEGORIES
from app.services.reconciliation import (
    load_events_for_pids,
    load_mp_expenses,
    load_mp_expenses_for_pids,
    load_payment_events,
    load_extrato,
    filter_stale_mp_expenses,
)

# Hardcoded CA category UUIDs for non-CA_CATEGORIES entries used by the
# extrato_ingester (see _CA_CATEGORY_CODE_MAP in extrato_ingester.py).
DIFAL_UUID = "3b1acab2-9fd6-4fce-b9ac-d418c6355c5d"


def _D(v) -> Decimal:
    return Decimal(str(v or 0)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _money(v: Decimal) -> str:
    sign = "-" if v < 0 else " "
    abs_v = abs(v)
    inteiro, _, dec = f"{abs_v:.2f}".partition(".")
    parts = []
    while len(inteiro) > 3:
        parts.insert(0, inteiro[-3:])
        inteiro = inteiro[:-3]
    parts.insert(0, inteiro)
    return f"R$ {sign}{'.'.join(parts)},{dec}"


# DRE row catalogue: (code, label, ca_category_uuid, dre_section)
DRE_ROWS: list[tuple[str, str, str, str]] = [
    # RECEITAS BRUTAS
    ("1.1.1", "Vendas Mercado Livre",                      CA_CATEGORIES["venda_ml"],          "receita_bruta"),
    ("1.1.2", "Vendas Loja Própria (E-commerce)",          CA_CATEGORIES["venda_ecommerce"],   "receita_bruta"),

    # DEDUÇÕES DA RECEITA
    ("1.2.1", "Devoluções e Cancelamentos",                CA_CATEGORIES["devolucao"],         "deducoes"),

    # OUTRAS RECEITAS / ESTORNOS
    ("1.3.4", "Estornos de Taxas e Tarifas",               CA_CATEGORIES["estorno_taxa"],      "outras_receitas"),
    ("1.3.7", "Estorno de Frete sobre Vendas",             CA_CATEGORIES["estorno_frete"],     "outras_receitas"),

    # DESPESAS DE VENDAS / TRIBUTOS
    ("2.2.3", "DIFAL (Diferencial de Alíquota)",           DIFAL_UUID,                          "tributos"),
    ("2.8.2", "Comissões de Marketplace",                  CA_CATEGORIES["comissao_ml"],       "despesas_vendas"),
    ("2.9.4", "MercadoEnvios",                             CA_CATEGORIES["frete_mercadoenvios"],"despesas_vendas"),
    ("2.9.10","Frete Full",                                CA_CATEGORIES["frete_full"],        "despesas_vendas"),
    ("2.11.8","Tarifas de Pagamento",                      CA_CATEGORIES["tarifa_pagamento"],  "despesas_financeiras"),
    ("2.11.9","Antecipação de Recebíveis",                 CA_CATEGORIES["antecipacao"],       "despesas_financeiras"),
]

CATEGORY_BY_UUID = {row[2]: row for row in DRE_ROWS}

# Map mp_expense.expense_type → ca_category_uuid (mirroring extrato_ingester
# _CA_CATEGORY_CODE_MAP plus expense_classifier defaults).
EXPENSE_TYPE_TO_CA: dict[str, str] = {
    "reembolso_disputa":        CA_CATEGORIES["estorno_taxa"],
    "reembolso_generico":       CA_CATEGORIES["estorno_taxa"],
    "bonus_envio":              CA_CATEGORIES["estorno_frete"],
    "cashback":                 CA_CATEGORIES["estorno_frete"],
    "difal":                    DIFAL_UUID,
    "faturas_ml":               CA_CATEGORIES["comissao_ml"],
    "debito_envio_ml":          CA_CATEGORIES["frete_mercadoenvios"],
    "debito_divida_disputa":    CA_CATEGORIES["devolucao"],
    "debito_troca":             CA_CATEGORIES["devolucao"],
    # Non-DRE flow-through (treasury / wash):
    # transfer_intra, transferencia_pix_*, deposit, dinheiro_retido,
    # entrada_dinheiro, pagamento_conta, pagamento_cartao_credito,
    # subscription, emprestimo_mp, renda_*, reserva_subconta — these are
    # cash movements without DRE impact (or treated outside this simulation).
}

# Event types that move cash AND have DRE impact
CASH_EVENT_DRE_MAP: dict[str, str] = {
    "sale_approved":     CA_CATEGORIES["venda_ml"],
    "fee_charged":       CA_CATEGORIES["comissao_ml"],
    "shipping_charged":  CA_CATEGORIES["frete_mercadoenvios"],
    "refund_created":    CA_CATEGORIES["devolucao"],
    "refund_fee":        CA_CATEGORIES["estorno_taxa"],
    "refund_shipping":   CA_CATEGORIES["estorno_frete"],
    "subsidy_credited":  CA_CATEGORIES["estorno_frete"],
}


def main(seller: str, period: str) -> int:
    import calendar
    y, m = int(period[:4]), int(period[5:7])
    last = calendar.monthrange(y, m)[1]
    period_start = f"{period}-01"
    period_end = f"{period}-{last:02d}"

    db = get_db()
    summary, txs = load_extrato(seller, period)
    extrato_pids = {int(tx["reference_id"]) for tx in txs if str(tx["reference_id"]).isdigit()}

    events = load_payment_events(db, seller, period_start, period_end)
    current = {int(e["ml_payment_id"]) for e in events if e.get("ml_payment_id")}
    extra = list(extrato_pids - current)
    if extra:
        events.extend(load_events_for_pids(db, seller, extra))

    expenses = load_mp_expenses(db, seller, period_start, period_end)
    in_period_pids = {
        str(ex.get("payment_id") or "").split(":")[0] for ex in expenses if ex.get("payment_id")
    }
    extra_expense_pids = [p for p in extrato_pids if str(p) not in in_period_pids]
    if extra_expense_pids:
        expenses.extend(load_mp_expenses_for_pids(db, seller, extra_expense_pids))

    approved = {int(e["ml_payment_id"]) for e in events
                if e.get("event_type") == "sale_approved" and e.get("ml_payment_id")}
    extrato_pids_str = {str(p) for p in extrato_pids}
    expenses = filter_stale_mp_expenses(expenses, approved, extrato_pids_str)

    # Aggregate by CA category UUID.
    # Convention: sum of signed_amounts. Receita keeps positive, despesas
    # come back negative (we present them as positive in their own column).
    totals: dict[str, Decimal] = defaultdict(lambda: _D(0))
    counts: dict[str, int] = defaultdict(int)

    # 1) payment_events (within period via competencia_date OR event_date)
    for ev in events:
        et = ev.get("event_type")
        ca_cat = CASH_EVENT_DRE_MAP.get(et)
        if not ca_cat:
            continue
        # filter by competencia_date in period
        comp = (ev.get("competencia_date") or ev.get("event_date") or "")[:10]
        if not (period_start <= comp <= period_end):
            continue
        amt = _D(ev.get("signed_amount"))
        totals[ca_cat] += amt
        counts[ca_cat] += 1

    # 2) mp_expenses (within period)
    for ex in expenses:
        et = ex.get("expense_type", "")
        ca_cat = EXPENSE_TYPE_TO_CA.get(et)
        if not ca_cat:
            continue
        date_app = (ex.get("date_approved") or "")[:10]
        if not (period_start <= date_app <= period_end):
            continue
        direction = ex.get("expense_direction", "")
        amount = _D(ex.get("amount"))
        if direction == "expense":
            signed = -amount
        else:
            signed = amount
        totals[ca_cat] += signed
        counts[ca_cat] += 1

    # ---- Render DRE ----
    sections = {
        "receita_bruta":        ("RECEITA BRUTA",          1),
        "deducoes":             ("(-) DEDUÇÕES",            -1),
        "outras_receitas":      ("(+) OUTRAS RECEITAS",     1),
        "tributos":             ("(-) TRIBUTOS",            -1),
        "despesas_vendas":      ("(-) DESPESAS DE VENDAS",  -1),
        "despesas_financeiras": ("(-) DESPESAS FINANCEIRAS",-1),
    }

    print(f"\n=== DRE Simulado — {seller} {period} ===")
    print(f"    Período: {period_start} a {period_end}")
    print(f"    Linhas extrato: {len(txs)}  |  Eventos sistema: {len(events)}  |  mp_expenses: {len(expenses)}")
    print()
    print(f"  {'Código':<8} {'Categoria':<45} {'Lançamentos':>11}    {'Valor':>16}")
    print(f"  {'-'*8} {'-'*45} {'-'*11}    {'-'*16}")

    section_totals: dict[str, Decimal] = defaultdict(lambda: _D(0))

    for sec_key, (sec_label, sign) in sections.items():
        sec_rows = [r for r in DRE_ROWS if r[3] == sec_key]
        if not sec_rows:
            continue
        section_total = _D(0)
        section_count = 0
        any_value = False
        for code, label, uuid, _ in sec_rows:
            value = totals.get(uuid, _D(0))
            cnt = counts.get(uuid, 0)
            if cnt > 0 or value != 0:
                any_value = True
            # For "deduções" / "despesas" sections, value is already negative;
            # we display absolute and accumulate absolute into section subtotal.
            if sign == -1:
                # Internal value is signed (negative). Show absolute.
                presented = -value if value < 0 else value
                section_total += value  # keep signed for net calc
            else:
                presented = value
                section_total += value
            section_count += cnt
            if cnt > 0 or value != 0:
                print(f"  {code:<8} {label[:45]:<45} {cnt:>11}    {_money(presented):>16}")
        if any_value:
            print(f"  {'':<8} {sec_label + ' (Subtotal)':<45} {section_count:>11}    {_money(section_total):>16}")
            print()
        section_totals[sec_key] = section_total

    # Net result
    receita_liquida = (
        section_totals["receita_bruta"]
        + section_totals["deducoes"]
        + section_totals["outras_receitas"]
    )
    resultado_operacional = (
        receita_liquida
        + section_totals["tributos"]
        + section_totals["despesas_vendas"]
        + section_totals["despesas_financeiras"]
    )

    print(f"  {'-'*8} {'-'*45} {'-'*11}    {'-'*16}")
    print(f"  {'':<8} {'(=) RECEITA LÍQUIDA':<45} {'':>11}    {_money(receita_liquida):>16}")
    print(f"  {'':<8} {'(=) RESULTADO OPERACIONAL':<45} {'':>11}    {_money(resultado_operacional):>16}")
    print()

    # JSON for downstream consumption
    json_payload = {
        "seller": seller,
        "period": period,
        "rows": [
            {
                "code": code,
                "label": label,
                "section": section,
                "ca_category_uuid": uuid,
                "lancamentos": counts.get(uuid, 0),
                "valor": float(totals.get(uuid, _D(0))),
            }
            for code, label, uuid, section in DRE_ROWS
        ],
        "subtotals": {k: float(v) for k, v in section_totals.items()},
        "receita_liquida": float(receita_liquida),
        "resultado_operacional": float(resultado_operacional),
    }
    out = PROJECT_ROOT / "docs" / "reconciliation" / f"dre_{seller}_{period}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(json_payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"  → JSON salvo em {out.relative_to(PROJECT_ROOT)}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: simulate_dre.py <seller> <period>", file=sys.stderr)
        sys.exit(2)
    sys.exit(main(sys.argv[1], sys.argv[2]))
