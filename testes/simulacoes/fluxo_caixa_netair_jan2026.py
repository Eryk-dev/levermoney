"""
Fluxo de Caixa — NET AIR — Janeiro 2026
Fonte: extrato oficial do MercadoPago (account_statement)

Lógica:
  - PARTIAL_BALANCE é o saldo após cada transação → dá saldo inicial/final por dia
  - Transações positivas  → Receitas (liberações, reembolsos, entradas)
  - Transações negativas  → Despesas (débitos, transferências, pagamentos)
  - Agrupamento por categoria para facilitar conferência com o painel MP
"""

import csv
import re
from collections import defaultdict
from pathlib import Path

EXTRATO_PATH = Path(__file__).parent / "extratos" / "extrato janeiro netair.csv"

# ──────────────────────────────────────────────────────────────
# Categorias (agrupa TRANSACTION_TYPE em grupos legíveis)
# ──────────────────────────────────────────────────────────────
def categorize(tx_type: str) -> tuple[str, str]:
    """Retorna (grupo, direcao) onde direcao='receita'|'despesa'|'neutro'"""
    t = tx_type.strip()

    if t.startswith("Liberação de dinheiro cancelada"):
        return "Liberação cancelada", "despesa"
    if t.startswith("Cancelamento da liberação"):
        return "Liberação cancelada", "despesa"
    if "Dinheiro recebido cancelado" in t:
        return "Entrada cancelada", "despesa"
    if t.startswith("Liberação de dinheiro"):
        return "Liberação de venda", "receita"
    if t.startswith("Entrada de dinheiro"):
        return "Entrada de dinheiro", "receita"
    if t.startswith("Dinheiro recebido"):
        return "Dinheiro recebido", "receita"
    if "Reembolso de tarifas" in t:
        return "Reembolso de tarifa", "receita"
    if t.startswith("Reembolso"):
        return "Reembolso / Devolução", "receita"
    if "Dinheiro retido" in t:
        return "Dinheiro retido", "despesa"
    if "Débito por dívida/dinheiro retido" in t:
        return "Dinheiro retido", "despesa"
    if "DIFAL" in t:
        return "Débito DIFAL", "despesa"
    if "Débito por dívida Envio" in t:
        return "Débito Envio ML", "despesa"
    if t.startswith("Débito por dívida"):
        return "Débito por dívida", "despesa"
    if t.startswith("Débito por troca"):
        return "Débito troca", "despesa"
    if t.startswith("Transferência Pix enviada"):
        return "Transferência PIX saída", "despesa"
    if "Pagamento de conta" in t:
        return "Pagamento de conta (boleto)", "despesa"
    if "Pagamento com" in t or "Pagamento QR" in t.lower():
        return "Pagamento QR/PIX", "despesa"
    # fallback
    return t[:50], "neutro"


# ──────────────────────────────────────────────────────────────
# Parse do extrato
# ──────────────────────────────────────────────────────────────
def parse_br_decimal(s: str) -> float:
    """Converte '1.234,56' → 1234.56 e '-1.234,56' → -1234.56"""
    s = s.strip().replace(".", "").replace(",", ".")
    return float(s) if s else 0.0


def load_extrato(path: Path):
    rows = []
    with open(path, encoding="utf-8-sig") as f:
        reader = csv.reader(f, delimiter=";")
        header_found = False
        for row in reader:
            if not row:
                continue
            if row[0] == "RELEASE_DATE":
                header_found = True
                continue
            if not header_found:
                continue
            if len(row) < 5:
                continue
            date_str = row[0].strip()
            tx_type  = row[1].strip()
            ref_id   = row[2].strip()
            amount   = parse_br_decimal(row[3])
            balance  = parse_br_decimal(row[4])

            # normaliza data DD-MM-YYYY → YYYY-MM-DD
            m = re.match(r"(\d{2})-(\d{2})-(\d{4})", date_str)
            if not m:
                continue
            date = f"{m.group(3)}-{m.group(2)}-{m.group(1)}"
            rows.append({
                "date": date,
                "tx_type": tx_type,
                "ref_id": ref_id,
                "amount": amount,
                "balance": balance,
            })
    return rows


# ──────────────────────────────────────────────────────────────
# Monta fluxo de caixa dia a dia
# ──────────────────────────────────────────────────────────────
def build_cashflow(rows):
    # Filtra apenas janeiro 2026
    jan = [r for r in rows if r["date"].startswith("2026-01")]

    # Saldo inicial do mês = balance da primeira linha - amount da primeira linha
    # (ou usar o cabeçalho INITIAL_BALANCE)
    if not jan:
        return []

    # Agrupar por dia
    by_day = defaultdict(list)
    for r in jan:
        by_day[r["date"]].append(r)

    days_sorted = sorted(by_day.keys())

    # Saldo inicial do mês (vem antes da primeira transação de jan)
    # Reconstruimos: saldo antes do 1º dia = balance_1a_tx - amount_1a_tx
    first_tx = by_day[days_sorted[0]][0]
    saldo_inicial_mes = first_tx["balance"] - first_tx["amount"]

    cashflow = []
    saldo_running = saldo_inicial_mes

    for day in days_sorted:
        txs = by_day[day]

        saldo_inicial_dia = saldo_running

        receitas_by_cat  = defaultdict(float)
        despesas_by_cat  = defaultdict(float)
        total_receitas   = 0.0
        total_despesas   = 0.0

        for tx in txs:
            cat, direcao = categorize(tx["tx_type"])
            amt = tx["amount"]

            if amt >= 0:
                receitas_by_cat[cat] += amt
                total_receitas += amt
            else:
                despesas_by_cat[cat] += amt
                total_despesas += amt

        saldo_final_dia = txs[-1]["balance"]  # último PARTIAL_BALANCE do dia
        saldo_running   = saldo_final_dia

        cashflow.append({
            "date": day,
            "saldo_inicial": saldo_inicial_dia,
            "total_receitas": total_receitas,
            "total_despesas": total_despesas,
            "saldo_final": saldo_final_dia,
            "receitas_by_cat": dict(receitas_by_cat),
            "despesas_by_cat": dict(despesas_by_cat),
            "n_tx": len(txs),
        })

    return cashflow


# ──────────────────────────────────────────────────────────────
# Formatação / Exibição
# ──────────────────────────────────────────────────────────────
def fmt(v: float) -> str:
    return f"R$ {v:>12,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def fmt_signed(v: float) -> str:
    s = fmt(abs(v))
    return f"+{s}" if v >= 0 else f"-{s}"


def print_cashflow(cashflow):
    W = 90
    SEP = "─" * W

    print()
    print("=" * W)
    print(" FLUXO DE CAIXA — NET AIR — JANEIRO 2026".center(W))
    print(" Fonte: extrato oficial MercadoPago (account_statement)".center(W))
    print("=" * W)

    total_receitas_mes  = sum(d["total_receitas"]  for d in cashflow)
    total_despesas_mes  = sum(d["total_despesas"]  for d in cashflow)
    saldo_inicial_mes   = cashflow[0]["saldo_inicial"]  if cashflow else 0
    saldo_final_mes     = cashflow[-1]["saldo_final"]   if cashflow else 0

    print()
    print(f"  Saldo inicial (01/01):  {fmt(saldo_inicial_mes)}")
    print(f"  Total receitas jan:     {fmt(total_receitas_mes)}")
    print(f"  Total despesas jan:     {fmt(total_despesas_mes)}")
    print(f"  Resultado líquido:      {fmt(total_receitas_mes + total_despesas_mes)}")
    print(f"  Saldo final (31/01):    {fmt(saldo_final_mes)}")
    print()
    print(SEP)

    for d in cashflow:
        dd = d["date"][8:10] + "/" + d["date"][5:7]
        resultado_dia = d["total_receitas"] + d["total_despesas"]
        sinal = "▲" if resultado_dia >= 0 else "▼"

        print()
        print(f"  {dd}/2026  ({d['n_tx']:3d} transações)   {sinal}  {fmt_signed(resultado_dia)}")
        print(f"  {'Saldo inicial:':<26} {fmt(d['saldo_inicial'])}")

        # Receitas por categoria
        if d["receitas_by_cat"]:
            print(f"  {'RECEITAS':}")
            for cat, val in sorted(d["receitas_by_cat"].items(), key=lambda x: -x[1]):
                print(f"      {cat:<40} {fmt(val)}")
            print(f"  {'  Total receitas:':<44} {fmt(d['total_receitas'])}")

        # Despesas por categoria
        if d["despesas_by_cat"]:
            print(f"  {'DESPESAS':}")
            for cat, val in sorted(d["despesas_by_cat"].items(), key=lambda x: x[1]):
                print(f"      {cat:<40} {fmt(val)}")
            print(f"  {'  Total despesas:':<44} {fmt(d['total_despesas'])}")

        print(f"  {'Saldo final:':<26} {fmt(d['saldo_final'])}")
        print(f"  {'':2}{SEP[2:]}")

    print()
    print("=" * W)
    print(" RESUMO POR CATEGORIA — JANEIRO COMPLETO".center(W))
    print("=" * W)

    # Agrega categorias do mês inteiro
    all_rec = defaultdict(float)
    all_desp = defaultdict(float)
    for d in cashflow:
        for cat, val in d["receitas_by_cat"].items():
            all_rec[cat] += val
        for cat, val in d["despesas_by_cat"].items():
            all_desp[cat] += val

    print()
    print("  RECEITAS")
    for cat, val in sorted(all_rec.items(), key=lambda x: -x[1]):
        print(f"      {cat:<44} {fmt(val)}")
    print(f"  {'  TOTAL RECEITAS':<48} {fmt(sum(all_rec.values()))}")

    print()
    print("  DESPESAS")
    for cat, val in sorted(all_desp.items(), key=lambda x: x[1]):
        print(f"      {cat:<44} {fmt(val)}")
    print(f"  {'  TOTAL DESPESAS':<48} {fmt(sum(all_desp.values()))}")

    print()
    print(f"  RESULTADO LÍQUIDO: {fmt(sum(all_rec.values()) + sum(all_desp.values()))}")
    print(f"  VARIAÇÃO DE SALDO: {fmt(saldo_final_mes - saldo_inicial_mes)}")
    print()
    print("=" * W)


# ──────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────
if __name__ == "__main__":
    rows = load_extrato(EXTRATO_PATH)
    cashflow = build_cashflow(rows)
    print_cashflow(cashflow)
