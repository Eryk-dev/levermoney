#!/usr/bin/env python3
"""
Full Reconciliation Simulation for 141air - February 2026
Classifies ALL extrato lines following legacy API rules.
Shows what would go into PAGAMENTO_CONTAS.xlsx, TRANSFERENCIAS.xlsx,
and what the API already handles (CONFIRMADOS).

NO data is sent to Conta Azul.
"""

from collections import defaultdict

# ============================================================
# PARSE EXTRATO
# ============================================================

EXTRATO_FILE = "/Volumes/SSD Eryk/financeiro v2/lever money claude v3/api nova/testes/extratos 141air/account_statement-6e669823-8bc7-4ad9-b9a3-95703e5e6b04.csv"

CENTRO_CUSTO = "141AIR"

# Legacy CA categories
CA_CATS = {
    'RECEITA_ML': "1.1.1 MercadoLibre",
    'ESTORNO_TAXA': "1.3.4 Descontos e Estornos de Taxas e Tarifas",
    'ESTORNO_FRETE': "1.3.7 Estorno de Frete sobre Vendas",
    'DEVOLUCAO': "1.2.1 Devoluções e Cancelamentos",
    'COMISSAO': "2.8.2 Comissões de Marketplace",
    'FRETE_ENVIO': "2.9.4 MercadoEnvios",
    'FRETE_REVERSO': "2.9.10 Logística Reversa",
    'DIFAL': "2.2.3 DIFAL (Diferencial de Alíquota)",
    'PAGAMENTO_CONTA': "2.1.1 Compra de Mercadorias",
    'MARKETING_ML': "2.7.3 Marketing em Marketplace",
    'OUTROS': "2.14.8 Despesas Eventuais",
    'TRANSFERENCIA': "Transferências",
}

def parse_amount(s):
    return float(s.replace(".", "").replace(",", "."))

def parse_date_br(date_str):
    """Convert dd-mm-yyyy to dd/mm/yyyy"""
    parts = date_str.split("-")
    if len(parts) == 3:
        return f"{parts[0]}/{parts[1]}/{parts[2]}"
    return date_str

extrato_lines = []
with open(EXTRATO_FILE, "r", encoding="utf-8") as f:
    lines = f.readlines()
    start = 0
    for i, line in enumerate(lines):
        if line.startswith("RELEASE_DATE"):
            start = i + 1
            break
    for line in lines[start:]:
        line = line.strip()
        if not line:
            continue
        parts = line.split(";")
        if len(parts) < 5:
            continue
        extrato_lines.append({
            "date": parts[0],
            "date_br": parse_date_br(parts[0]),
            "type": parts[1],
            "ref_id": parts[2],
            "amount": parse_amount(parts[3]),
            "balance": parse_amount(parts[4]),
        })

# ============================================================
# CLASSIFY EACH LINE (following legacy API rules exactly)
# ============================================================

confirmados = []        # Vendas + refund flows (handled by API or need backfill)
pagamento_contas = []   # Bill payments, DARFs, subscriptions, purchases
transferencias = []     # PIX transfers, savings pot, card payments

for line in extrato_lines:
    t = line["type"]
    tipo_lower = t.lower()
    val = line["amount"]
    ref_id = line["ref_id"]
    date_br = line["date_br"]
    descricao = f"{ref_id} - {t[:50]}"

    entry = {
        "ID Operação": ref_id,
        "Data de Competência": date_br,
        "Data de Pagamento": date_br,
        "Valor": round(val, 2),
        "Centro de Custo": CENTRO_CUSTO,
        "Descrição": descricao,
    }

    # Ignore zero values
    if abs(val) < 0.01:
        continue

    # --- CATEGORIA 1: TRANSFERÊNCIAS ---
    if 'ransfer' in tipo_lower:
        # "Transferência Pix enviada" or "Pix enviado" (caught by ransfer in "Transferência")
        entry["Categoria"] = CA_CATS['TRANSFERENCIA']
        entry["Observações"] = t
        transferencias.append(entry)
        continue

    # --- PIX ENVIADO (also transfer) ---
    if 'pix enviado' in tipo_lower:
        entry["Categoria"] = CA_CATS['TRANSFERENCIA']
        entry["Observações"] = t
        transferencias.append(entry)
        continue

    # --- DINHEIRO RESERVADO (savings pot = transfer) ---
    if 'dinheiro reservado' in tipo_lower:
        entry["Categoria"] = CA_CATS['TRANSFERENCIA']
        entry["Observações"] = "Reserva de dinheiro (Renda MP)"
        transferencias.append(entry)
        continue

    # --- LIBERAÇÃO CANCELADA ---
    if 'liberação de dinheiro cancelada' in tipo_lower or 'liberacao de dinheiro cancelada' in tipo_lower:
        if val > 0:
            entry["Categoria"] = CA_CATS['ESTORNO_TAXA']
            entry["Observações"] = "Estorno de liberação cancelada"
        else:
            entry["Categoria"] = CA_CATS['DEVOLUCAO']
            entry["Observações"] = "Liberação cancelada (chargeback)"
        confirmados.append(entry)
        continue

    # --- PAGAMENTO FATURA CARTÃO MP ---
    if 'pagamento' in tipo_lower and 'cartão de crédito' in tipo_lower:
        entry["Categoria"] = CA_CATS['TRANSFERENCIA']
        entry["Observações"] = "Pagamento fatura cartão Mercado Pago"
        transferencias.append(entry)
        continue

    # --- LIBERAÇÃO DE DINHEIRO (VENDA) ---
    if 'liberação de dinheiro' in tipo_lower or 'liberacao de dinheiro' in tipo_lower:
        entry["Categoria"] = CA_CATS['RECEITA_ML']
        entry["Observações"] = "Liberação de venda"
        confirmados.append(entry)
        continue

    # --- REEMBOLSO ---
    if 'reembolso' in tipo_lower:
        if val > 0:
            entry["Categoria"] = CA_CATS['ESTORNO_TAXA']
            entry["Observações"] = "Estorno/Reembolso"
        else:
            entry["Categoria"] = CA_CATS['DEVOLUCAO']
            entry["Observações"] = "Devolução ao comprador"
        confirmados.append(entry)
        continue

    # --- DINHEIRO RETIDO ---
    if 'dinheiro retido' in tipo_lower:
        if val < 0:
            entry["Categoria"] = CA_CATS['DEVOLUCAO']
            entry["Observações"] = "Dinheiro retido (bloqueio por disputa)"
        else:
            entry["Categoria"] = CA_CATS['ESTORNO_TAXA']
            entry["Observações"] = "Dinheiro liberado (desbloqueio)"
        confirmados.append(entry)
        continue

    # --- PAGAMENTO DE CONTAS ---
    if 'pagamento de contas' in tipo_lower or 'pagamento de conta' in tipo_lower:
        entry["Categoria"] = CA_CATS['PAGAMENTO_CONTA']
        entry["Observações"] = "Pagamento de conta via MP"
        pagamento_contas.append(entry)
        continue

    # --- PAGAMENTO / QR (PIX) ---
    if 'pagamento' in tipo_lower or 'qr' in tipo_lower:
        if val < 0:
            # Pagamento enviado (DARF, subscription, etc.)
            entry["Categoria"] = CA_CATS['PAGAMENTO_CONTA']
            entry["Observações"] = f"Pagamento enviado via PIX/QR - {t[:60]}"
            pagamento_contas.append(entry)
        else:
            # Pagamento recebido (venda via PIX/QR)
            entry["Categoria"] = CA_CATS['RECEITA_ML']
            entry["Observações"] = "Pagamento recebido via PIX/QR"
            confirmados.append(entry)
        continue

    # --- ENTRADA DE DINHEIRO ---
    if 'entrada' in tipo_lower:
        entry["Categoria"] = CA_CATS['RECEITA_ML']
        entry["Observações"] = "Entrada de dinheiro"
        confirmados.append(entry)
        continue

    # --- DÉBITOS (reclamação, envio, etc.) ---
    if 'débito' in tipo_lower or 'debito' in tipo_lower or 'dívida' in tipo_lower or 'divida' in tipo_lower:
        if 'reclama' in tipo_lower:
            entry["Categoria"] = CA_CATS['DEVOLUCAO']
            entry["Observações"] = "Débito por reclamação ML"
        elif 'envio' in tipo_lower:
            entry["Categoria"] = CA_CATS['FRETE_ENVIO']
            entry["Observações"] = "Débito de envio"
        elif 'fatura' in tipo_lower:
            entry["Categoria"] = CA_CATS['MARKETING_ML']
            entry["Observações"] = "Product ADS"
        else:
            entry["Categoria"] = CA_CATS['OUTROS']
            entry["Observações"] = "Débito/Dívida ML"
        confirmados.append(entry)
        continue

    # --- BÔNUS DE ENVIO ---
    if 'bônus' in tipo_lower or 'bonus' in tipo_lower:
        entry["Categoria"] = CA_CATS['ESTORNO_FRETE']
        entry["Observações"] = "Bônus de envio"
        confirmados.append(entry)
        continue

    # --- DINHEIRO RECEBIDO (Proteção Full, etc.) ---
    if 'dinheiro recebido' in tipo_lower:
        entry["Categoria"] = CA_CATS['RECEITA_ML']
        entry["Observações"] = "Dinheiro recebido"
        confirmados.append(entry)
        continue

    # --- NÃO CLASSIFICADO ---
    entry["Categoria"] = CA_CATS['OUTROS']
    entry["Observações"] = f"REVISAR: {t[:30]}"
    confirmados.append(entry)

# ============================================================
# PRINT RESULTS
# ============================================================

def print_table(title, rows, show_obs=True):
    if not rows:
        print(f"\n{title}: (vazio)")
        return

    total = sum(r["Valor"] for r in rows)
    print(f"\n{'='*120}")
    print(f"{title} ({len(rows)} linhas, Total: R${total:,.2f})")
    print(f"{'='*120}")

    if show_obs:
        print(f"{'Data':<12} {'ID Operação':>15} {'Valor':>12} {'Categoria':<45} {'Observações':<50}")
        print("-" * 140)
        for r in rows:
            print(f"{r['Data de Pagamento']:<12} {r['ID Operação']:>15} {r['Valor']:>12,.2f} {r['Categoria']:<45} {r.get('Observações','')[:50]}")
    else:
        print(f"{'Data':<12} {'ID Operação':>15} {'Valor':>12} {'Categoria':<45}")
        print("-" * 90)
        for r in rows:
            print(f"{r['Data de Pagamento']:<12} {r['ID Operação']:>15} {r['Valor']:>12,.2f} {r['Categoria']:<45}")

    print("-" * 90)
    print(f"{'TOTAL':<12} {'':>15} {total:>12,.2f}")

# ============================================================
# PAGAMENTO_CONTAS.xlsx
# ============================================================
print_table("PAGAMENTO_CONTAS.xlsx", pagamento_contas)

# ============================================================
# TRANSFERENCIAS.xlsx
# ============================================================
print_table("TRANSFERENCIAS.xlsx", transferencias)

# ============================================================
# CONFIRMADOS (handled by API for vendas)
# ============================================================

# Group confirmados by category for summary
cat_summary = defaultdict(lambda: {"count": 0, "total": 0.0})
for r in confirmados:
    c = r["Categoria"]
    cat_summary[c]["count"] += 1
    cat_summary[c]["total"] += r["Valor"]

print(f"\n{'='*120}")
print(f"CONFIRMADOS - RESUMO POR CATEGORIA ({len(confirmados)} linhas)")
print(f"{'='*120}")
print(f"{'Categoria':<50} {'Qtd':>6} {'Total R$':>14}")
print("-" * 75)
for cat, info in sorted(cat_summary.items(), key=lambda x: abs(x[1]["total"]), reverse=True):
    print(f"{cat:<50} {info['count']:>6} {info['total']:>14,.2f}")
total_conf = sum(r["Valor"] for r in confirmados)
print("-" * 75)
print(f"{'TOTAL':<50} {len(confirmados):>6} {total_conf:>14,.2f}")

# ============================================================
# DAILY RECONCILIATION
# ============================================================

daily = defaultdict(lambda: {"conf": 0.0, "pag": 0.0, "trans": 0.0, "extrato": 0.0})

for r in confirmados:
    # Convert date_br back to raw date for grouping
    d = r["Data de Pagamento"]
    daily[d]["conf"] += r["Valor"]

for r in pagamento_contas:
    d = r["Data de Pagamento"]
    daily[d]["pag"] += r["Valor"]

for r in transferencias:
    d = r["Data de Pagamento"]
    daily[d]["trans"] += r["Valor"]

# Also compute extrato total per day
for line in extrato_lines:
    d = line["date_br"]
    daily[d]["extrato"] += line["amount"]

print(f"\n{'='*120}")
print(f"RECONCILIAÇÃO DIÁRIA (extrato_total = confirmados + pagamentos + transferências)")
print(f"{'='*120}")
print(f"{'Data':<12} {'Extrato':>12} {'Confirmados':>14} {'Pag.Contas':>14} {'Transf.':>14} {'Diff':>12}")
print("-" * 80)

total_ext = total_conf_d = total_pag_d = total_trans_d = 0.0

for d in sorted(daily.keys(), key=lambda x: x.split("/")[::-1]):
    dd = daily[d]
    diff = dd["extrato"] - (dd["conf"] + dd["pag"] + dd["trans"])
    total_ext += dd["extrato"]
    total_conf_d += dd["conf"]
    total_pag_d += dd["pag"]
    total_trans_d += dd["trans"]
    print(f"{d:<12} {dd['extrato']:>12,.2f} {dd['conf']:>14,.2f} {dd['pag']:>14,.2f} {dd['trans']:>14,.2f} {diff:>12,.2f}")

total_diff = total_ext - (total_conf_d + total_pag_d + total_trans_d)
print("-" * 80)
print(f"{'TOTAL':<12} {total_ext:>12,.2f} {total_conf_d:>14,.2f} {total_pag_d:>14,.2f} {total_trans_d:>14,.2f} {total_diff:>12,.2f}")

# ============================================================
# GRAND SUMMARY
# ============================================================

print(f"\n{'='*120}")
print("RESUMO GERAL")
print(f"{'='*120}")
print(f"""
Extrato: R${total_ext:,.2f} (saldo inicial R$1.090,40 → final R$2.862,73 = diff R$1.772,33)
Confirmados: R${total_conf:,.2f} ({len(confirmados)} linhas) → API nova + ajustes
Pagamento de Contas: R${sum(r['Valor'] for r in pagamento_contas):,.2f} ({len(pagamento_contas)} linhas) → XLSX
Transferências: R${sum(r['Valor'] for r in transferencias):,.2f} ({len(transferencias)} linhas) → XLSX
Diferença (deve ser 0): R${total_diff:,.2f}

Se Diff = 0, a classificação está completa e nenhuma linha ficou de fora.
""")
