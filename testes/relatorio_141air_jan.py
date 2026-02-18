#!/usr/bin/env python3
"""
Relatório completo 141AIR — Janeiro 2026
DRE (competência) + Fluxo de Caixa (extrato) + Comparativo diário

Busca ALL payments via ML API, calcula receita/comissão/frete,
e compara com extrato account_statement.
"""

import json, sys, time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
import httpx

# ============================================================
# CONFIG
# ============================================================

ML_APP_ID = "8723807554954113"
ML_SECRET = "qT1qjZkpTHnu2HSOhAWQ860BgaofMCQq"
ML_REFRESH_TOKEN = "TG-698e291177af74000173618e-1963376627"

EXTRATO_FILE = "/Volumes/SSD Eryk/financeiro v2/lever money claude v3/api nova/testes/extratos 141air/account_statement-3675d9ed-726c-4bbe-ac34-42f5d0743bf1.csv"

SELLER_ML_USER_ID = 1963376627
CENTRO_CUSTO = "141AIR"

BRT = timezone(timedelta(hours=-3))
ML_TZ = timezone(timedelta(hours=-4))  # ML API returns UTC-4

# ============================================================
# 1. GET FRESH TOKEN
# ============================================================

def get_fresh_token():
    with httpx.Client(timeout=30) as client:
        resp = client.post("https://api.mercadopago.com/oauth/token", json={
            "grant_type": "refresh_token",
            "client_id": ML_APP_ID,
            "client_secret": ML_SECRET,
            "refresh_token": ML_REFRESH_TOKEN,
        })
        resp.raise_for_status()
        return resp.json()["access_token"]

print("Refreshing ML token...")
TOKEN = get_fresh_token()
print("OK\n")

# ============================================================
# 2. FETCH ALL PAYMENTS (Jan 1-31, by date_approved + date_last_updated)
# ============================================================

def fetch_all_payments(token, begin, end, range_field="date_approved"):
    all_payments = []
    offset = 0
    with httpx.Client(timeout=30) as client:
        while True:
            resp = client.get(
                "https://api.mercadopago.com/v1/payments/search",
                headers={"Authorization": f"Bearer {token}"},
                params={
                    "sort": range_field,
                    "criteria": "asc",
                    "range": range_field,
                    "begin_date": begin,
                    "end_date": end,
                    "offset": offset,
                    "limit": 50,
                },
            )
            resp.raise_for_status()
            data = resp.json()
            results = data.get("results", [])
            total = data.get("paging", {}).get("total", 0)
            all_payments.extend(results)
            offset += len(results)
            if offset >= total or not results:
                break
            time.sleep(0.3)
    return all_payments

print("Fetching payments by date_approved Jan 1-31...")
by_approved = fetch_all_payments(
    TOKEN,
    "2026-01-01T00:00:00.000-03:00",
    "2026-01-31T23:59:59.999-03:00",
    "date_approved",
)
print(f"  → {len(by_approved)} payments")

print("Fetching payments by date_last_updated Jan 1-31...")
by_updated = fetch_all_payments(
    TOKEN,
    "2026-01-01T00:00:00.000-03:00",
    "2026-01-31T23:59:59.999-03:00",
    "date_last_updated",
)
print(f"  → {len(by_updated)} payments")

# Deduplicate
payments_by_id = {}
for p in by_approved:
    payments_by_id[p["id"]] = p
for p in by_updated:
    payments_by_id[p["id"]] = p

all_payments = sorted(payments_by_id.values(), key=lambda p: p.get("date_approved") or "")
print(f"  → {len(all_payments)} unique payments\n")

# ============================================================
# 3. CLASSIFY PAYMENTS (same logic as processor.py)
# ============================================================

def to_brt_date(iso_str):
    """Convert ML ISO datetime (UTC-4) → BRT date string YYYY-MM-DD."""
    if not iso_str:
        return None
    dt = datetime.fromisoformat(iso_str)
    brt_dt = dt.astimezone(BRT)
    return brt_dt.strftime("%Y-%m-%d")

def extract_comissao(payment):
    """Extract commission from charges_details (same as processor.py)."""
    charges = payment.get("charges_details") or []
    total = 0.0
    for c in charges:
        if c.get("type") == "fee":
            accounts = c.get("accounts") or {}
            if accounts.get("from") == "collector" and c.get("name") != "financing_fee":
                total += float(c.get("amounts", {}).get("original", 0))
    return round(total, 2)

def extract_frete(payment):
    """Extract seller shipping cost from charges_details."""
    charges = payment.get("charges_details") or []
    shipping_collector = 0.0
    for c in charges:
        if c.get("type") == "shipping":
            accounts = c.get("accounts") or {}
            if accounts.get("from") == "collector":
                shipping_collector += float(c.get("amounts", {}).get("original", 0))
    shipping_amount = float(payment.get("shipping_amount") or 0)
    return round(max(0, shipping_collector - shipping_amount), 2)

# Classify each payment
vendas = []         # Has order_id, passes filters
non_orders = []     # No order_id
skipped = []        # Filtered out

for p in all_payments:
    pid = p["id"]
    status = p.get("status", "")
    status_detail = p.get("status_detail", "")
    order_id = (p.get("order") or {}).get("id")
    desc = p.get("description") or ""
    op_type = p.get("operation_type", "")
    collector_id = (p.get("collector") or {}).get("id")
    amount = float(p.get("transaction_amount") or 0)
    net = float(p.get("net_received_amount") or 0)
    date_approved = p.get("date_approved")
    money_release_date = (p.get("money_release_date") or "")[:10]
    refunded_amount = float(p.get("transaction_amount_refunded") or 0)

    competencia = to_brt_date(date_approved) if date_approved else None

    # Skip filters
    if op_type in ("partition_transfer", "payment_addition"):
        skipped.append({"id": pid, "reason": op_type, "amount": amount})
        continue

    if not order_id:
        non_orders.append(p)
        continue

    if desc == "marketplace_shipment":
        skipped.append({"id": pid, "reason": "marketplace_shipment", "amount": amount})
        continue

    if collector_id is not None and collector_id != SELLER_ML_USER_ID:
        skipped.append({"id": pid, "reason": f"collector={collector_id}", "amount": amount})
        continue

    if status in ("cancelled", "rejected"):
        skipped.append({"id": pid, "reason": f"status={status}", "amount": amount})
        continue

    comissao = extract_comissao(p)
    frete = extract_frete(p)

    venda = {
        "id": pid,
        "order_id": order_id,
        "status": status,
        "status_detail": status_detail,
        "amount": amount,
        "net": net,
        "comissao": comissao,
        "frete": frete,
        "competencia": competencia,
        "money_release_date": money_release_date,
        "refunded_amount": refunded_amount,
    }

    # Determine action
    if status == "approved" or status == "in_mediation":
        venda["action"] = "APPROVED"
    elif status == "charged_back" and status_detail == "reimbursed":
        venda["action"] = "APPROVED"  # ML covered it
    elif status == "refunded" and status_detail == "by_admin":
        venda["action"] = "SKIP_BY_ADMIN"
    elif status in ("refunded", "charged_back"):
        venda["action"] = "REFUNDED"
    else:
        venda["action"] = "SKIP"

    vendas.append(venda)

# ============================================================
# 4. BUILD DRE (by competência = date_approved BRT)
# ============================================================

dre = defaultdict(lambda: {
    "receitas": 0.0, "n_receitas": 0,
    "comissoes": 0.0, "n_comissoes": 0,
    "fretes": 0.0, "n_fretes": 0,
    "estornos": 0.0, "n_estornos": 0,
    "estorno_taxas": 0.0, "n_estorno_taxas": 0,
})

for v in vendas:
    comp = v["competencia"]
    if not comp:
        continue

    if v["action"] == "APPROVED":
        dre[comp]["receitas"] += v["amount"]
        dre[comp]["n_receitas"] += 1
        if v["comissao"] > 0:
            dre[comp]["comissoes"] += v["comissao"]
            dre[comp]["n_comissoes"] += 1
        if v["frete"] > 0:
            dre[comp]["fretes"] += v["frete"]
            dre[comp]["n_fretes"] += 1

    elif v["action"] == "REFUNDED":
        # Estorno = receita original (or refunded amount)
        estorno_val = v["refunded_amount"] if v["refunded_amount"] > 0 else v["amount"]
        dre[comp]["estornos"] += estorno_val
        dre[comp]["n_estornos"] += 1
        # Estorno taxa (refund total → devolve comissão+frete)
        if v["refunded_amount"] == 0 or abs(v["refunded_amount"] - v["amount"]) < 0.01:
            estorno_taxa = v["comissao"] + v["frete"]
            dre[comp]["estorno_taxas"] += estorno_taxa
            dre[comp]["n_estorno_taxas"] += 1
        # Also create original receita if refunded (processor does this)
        dre[comp]["receitas"] += v["amount"]
        dre[comp]["n_receitas"] += 1
        if v["comissao"] > 0:
            dre[comp]["comissoes"] += v["comissao"]
            dre[comp]["n_comissoes"] += 1
        if v["frete"] > 0:
            dre[comp]["fretes"] += v["frete"]
            dre[comp]["n_fretes"] += 1

    # SKIP_BY_ADMIN → don't create anything (kit split)

# ============================================================
# 5. PARSE EXTRATO (January)
# ============================================================

def parse_amount(s):
    return float(s.replace(".", "").replace(",", "."))

def parse_date_br(date_str):
    parts = date_str.split("-")
    if len(parts) == 3:
        return f"{parts[0]}/{parts[1]}/{parts[2]}"
    return date_str

extrato_lines = []
with open(EXTRATO_FILE, "r", encoding="utf-8") as f:
    lines = f.readlines()
    # Parse header
    header_line = lines[0].strip().split(";")
    header_vals = lines[1].strip().split(";")
    initial_balance = parse_amount(header_vals[0]) if len(header_vals) > 0 else 0
    final_balance = parse_amount(header_vals[3]) if len(header_vals) > 3 else 0

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

print(f"Extrato: {len(extrato_lines)} linhas, saldo {initial_balance:,.2f} → {final_balance:,.2f}")

# ============================================================
# 6. CLASSIFY EXTRATO (legacy rules)
# ============================================================

CA_CATS = {
    'RECEITA_ML': "1.1.1 MercadoLibre",
    'ESTORNO_TAXA': "1.3.4 Estornos de Taxas",
    'ESTORNO_FRETE': "1.3.7 Estorno de Frete",
    'DEVOLUCAO': "1.2.1 Devoluções",
    'COMISSAO': "2.8.2 Comissões Marketplace",
    'FRETE_ENVIO': "2.9.4 MercadoEnvios",
    'FRETE_REVERSO': "2.9.10 Logística Reversa",
    'DIFAL': "2.2.3 DIFAL",
    'PAGAMENTO_CONTA': "2.1.1 Compra de Mercadorias",
    'MARKETING_ML': "2.7.3 Marketing Marketplace",
    'OUTROS': "2.14.8 Despesas Eventuais",
    'TRANSFERENCIA': "Transferências",
}

confirmados = []
pagamento_contas = []
transferencias = []

for line in extrato_lines:
    t = line["type"]
    tipo_lower = t.lower()
    val = line["amount"]
    ref_id = line["ref_id"]
    date_br = line["date_br"]

    entry = {
        "ref_id": ref_id,
        "data": date_br,
        "valor": round(val, 2),
        "tipo_extrato": t[:60],
    }

    if abs(val) < 0.01:
        continue

    # TRANSFERÊNCIAS
    if 'ransfer' in tipo_lower:
        entry["categoria"] = CA_CATS['TRANSFERENCIA']
        entry["obs"] = t[:50]
        transferencias.append(entry)
        continue

    if 'pix enviado' in tipo_lower:
        entry["categoria"] = CA_CATS['TRANSFERENCIA']
        entry["obs"] = t[:50]
        transferencias.append(entry)
        continue

    if 'dinheiro reservado' in tipo_lower:
        entry["categoria"] = CA_CATS['TRANSFERENCIA']
        entry["obs"] = "Reserva dinheiro (Renda MP)"
        transferencias.append(entry)
        continue

    # LIBERAÇÃO CANCELADA
    if 'liberação de dinheiro cancelada' in tipo_lower or 'liberacao de dinheiro cancelada' in tipo_lower:
        entry["categoria"] = CA_CATS['ESTORNO_TAXA'] if val > 0 else CA_CATS['DEVOLUCAO']
        entry["obs"] = "Lib. cancelada (estorno)" if val > 0 else "Lib. cancelada (chargeback)"
        confirmados.append(entry)
        continue

    # PAGAMENTO CARTÃO MP
    if 'pagamento' in tipo_lower and 'cartão de crédito' in tipo_lower:
        entry["categoria"] = CA_CATS['TRANSFERENCIA']
        entry["obs"] = "Pagamento fatura cartão MP"
        transferencias.append(entry)
        continue

    # LIBERAÇÃO DE DINHEIRO (VENDA)
    if 'liberação de dinheiro' in tipo_lower or 'liberacao de dinheiro' in tipo_lower:
        entry["categoria"] = CA_CATS['RECEITA_ML']
        entry["obs"] = "Liberação de venda"
        confirmados.append(entry)
        continue

    # REEMBOLSO
    if 'reembolso' in tipo_lower:
        entry["categoria"] = CA_CATS['ESTORNO_TAXA'] if val > 0 else CA_CATS['DEVOLUCAO']
        entry["obs"] = "Estorno/Reembolso" if val > 0 else "Devolução ao comprador"
        confirmados.append(entry)
        continue

    # DINHEIRO RETIDO
    if 'dinheiro retido' in tipo_lower:
        entry["categoria"] = CA_CATS['ESTORNO_TAXA'] if val > 0 else CA_CATS['DEVOLUCAO']
        entry["obs"] = "Dinheiro liberado" if val > 0 else "Dinheiro retido (disputa)"
        confirmados.append(entry)
        continue

    # PAGAMENTO DE CONTAS
    if 'pagamento de contas' in tipo_lower or 'pagamento de conta' in tipo_lower:
        entry["categoria"] = CA_CATS['PAGAMENTO_CONTA']
        entry["obs"] = "Pagamento de conta via MP"
        pagamento_contas.append(entry)
        continue

    # PAGAMENTO / QR (PIX)
    if 'pagamento' in tipo_lower or 'qr' in tipo_lower:
        if val < 0:
            entry["categoria"] = CA_CATS['PAGAMENTO_CONTA']
            entry["obs"] = f"Pagamento enviado PIX/QR"
            pagamento_contas.append(entry)
        else:
            entry["categoria"] = CA_CATS['RECEITA_ML']
            entry["obs"] = "Pagamento recebido PIX/QR"
            confirmados.append(entry)
        continue

    # ENTRADA DE DINHEIRO
    if 'entrada' in tipo_lower:
        entry["categoria"] = CA_CATS['RECEITA_ML']
        entry["obs"] = "Entrada de dinheiro"
        confirmados.append(entry)
        continue

    # DÉBITOS
    if 'débito' in tipo_lower or 'debito' in tipo_lower or 'dívida' in tipo_lower or 'divida' in tipo_lower:
        if 'reclama' in tipo_lower:
            entry["categoria"] = CA_CATS['DEVOLUCAO']
            entry["obs"] = "Débito por reclamação"
        elif 'envio' in tipo_lower:
            entry["categoria"] = CA_CATS['FRETE_ENVIO']
            entry["obs"] = "Débito de envio"
        elif 'fatura' in tipo_lower:
            entry["categoria"] = CA_CATS['MARKETING_ML']
            entry["obs"] = "Product ADS"
        else:
            entry["categoria"] = CA_CATS['OUTROS']
            entry["obs"] = "Débito/Dívida ML"
        confirmados.append(entry)
        continue

    # BÔNUS DE ENVIO
    if 'bônus' in tipo_lower or 'bonus' in tipo_lower:
        entry["categoria"] = CA_CATS['ESTORNO_FRETE']
        entry["obs"] = "Bônus de envio"
        confirmados.append(entry)
        continue

    # DINHEIRO RECEBIDO
    if 'dinheiro recebido' in tipo_lower:
        entry["categoria"] = CA_CATS['RECEITA_ML']
        entry["obs"] = "Dinheiro recebido"
        confirmados.append(entry)
        continue

    # NÃO CLASSIFICADO
    entry["categoria"] = CA_CATS['OUTROS']
    entry["obs"] = f"REVISAR: {t[:30]}"
    confirmados.append(entry)

# ============================================================
# 7. PRINT DRE REPORT
# ============================================================

print("\n" + "=" * 130)
print("DRE — 141AIR JANEIRO 2026 (por competência = date_approved BRT)")
print("=" * 130)

# Summary by status
action_counts = defaultdict(lambda: {"count": 0, "amount": 0.0})
for v in vendas:
    action_counts[v["action"]]["count"] += 1
    action_counts[v["action"]]["amount"] += v["amount"]

print(f"\nPayments from ML API: {len(all_payments)} total")
print(f"  Vendas (com order): {len(vendas)}")
for a, d in sorted(action_counts.items()):
    print(f"    {a:<20} {d['count']:>4} vendas  R$ {d['amount']:>12,.2f}")
print(f"  Non-order:          {len(non_orders)}")
print(f"  Skipped:            {len(skipped)}")

print(f"\n{'Competência':<12} {'Receitas':>12} {'Comissões':>12} {'Fretes':>10} {'Estornos':>12} {'Est.Taxa':>10} {'Líquido':>12} {'#V':>4}")
print("-" * 100)

total_rec = total_com = total_frt = total_est = total_et = 0.0
total_nv = 0

for comp in sorted(dre.keys()):
    d = dre[comp]
    liq = d["receitas"] - d["comissoes"] - d["fretes"] - d["estornos"] + d["estorno_taxas"]
    total_rec += d["receitas"]
    total_com += d["comissoes"]
    total_frt += d["fretes"]
    total_est += d["estornos"]
    total_et += d["estorno_taxas"]
    total_nv += d["n_receitas"]
    print(f"{comp:<12} {d['receitas']:>12,.2f} {-d['comissoes']:>12,.2f} {-d['fretes']:>10,.2f} {-d['estornos']:>12,.2f} {d['estorno_taxas']:>10,.2f} {liq:>12,.2f} {d['n_receitas']:>4}")

total_liq = total_rec - total_com - total_frt - total_est + total_et
print("-" * 100)
print(f"{'TOTAL':<12} {total_rec:>12,.2f} {-total_com:>12,.2f} {-total_frt:>10,.2f} {-total_est:>12,.2f} {total_et:>10,.2f} {total_liq:>12,.2f} {total_nv:>4}")

print(f"""
RESUMO DRE:
  Receita bruta:     R$ {total_rec:>12,.2f}  ({total_nv} vendas)
  (-) Devoluções:    R$ {-total_est:>12,.2f}
  = Receita líquida: R$ {total_rec - total_est:>12,.2f}
  (-) Comissão ML:   R$ {-total_com:>12,.2f}
  (-) Frete envios:  R$ {-total_frt:>12,.2f}
  (+) Estorno taxas: R$ {total_et:>12,.2f}
  = Lucro op. ML:    R$ {total_liq:>12,.2f}
""")

# ============================================================
# 8. PRINT CASH FLOW (EXTRATO CLASSIFICATION)
# ============================================================

print("=" * 130)
print("FLUXO DE CAIXA — EXTRATO CLASSIFICADO (regras legado)")
print("=" * 130)

# Summary by category
cat_summary = defaultdict(lambda: {"count": 0, "total": 0.0})
for r in confirmados:
    cat_summary[r["categoria"]]["count"] += 1
    cat_summary[r["categoria"]]["total"] += r["valor"]

print(f"\nCONFIRMADOS (API + ajustes):")
print(f"{'Categoria':<50} {'Qtd':>6} {'Total R$':>14}")
print("-" * 75)
for cat in sorted(cat_summary.keys(), key=lambda x: abs(cat_summary[x]["total"]), reverse=True):
    info = cat_summary[cat]
    print(f"{cat:<50} {info['count']:>6} {info['total']:>14,.2f}")
total_conf = sum(r["valor"] for r in confirmados)
print("-" * 75)
print(f"{'SUBTOTAL CONFIRMADOS':<50} {len(confirmados):>6} {total_conf:>14,.2f}")

total_pag = sum(r["valor"] for r in pagamento_contas)
total_trans = sum(r["valor"] for r in transferencias)
print(f"\n{'PAGAMENTO DE CONTAS':<50} {len(pagamento_contas):>6} {total_pag:>14,.2f}")
print(f"{'TRANSFERÊNCIAS':<50} {len(transferencias):>6} {total_trans:>14,.2f}")
print(f"\n{'TOTAL GERAL EXTRATO':<50} {len(confirmados)+len(pagamento_contas)+len(transferencias):>6} {total_conf+total_pag+total_trans:>14,.2f}")
print(f"{'Variação saldo extrato':<50} {'':>6} {final_balance - initial_balance:>14,.2f}")

# ============================================================
# 9. DAILY RECONCILIATION (extrato vs classification)
# ============================================================

print(f"\n{'=' * 130}")
print("RECONCILIAÇÃO DIÁRIA — extrato_total = confirmados + pag.contas + transferências")
print(f"{'=' * 130}")

daily = defaultdict(lambda: {"extrato": 0.0, "conf": 0.0, "pag": 0.0, "trans": 0.0})

for line in extrato_lines:
    d = line["date_br"]
    daily[d]["extrato"] += line["amount"]

for r in confirmados:
    daily[r["data"]]["conf"] += r["valor"]
for r in pagamento_contas:
    daily[r["data"]]["pag"] += r["valor"]
for r in transferencias:
    daily[r["data"]]["trans"] += r["valor"]

print(f"\n{'Data':<12} {'Extrato':>12} {'Confirmados':>14} {'Pag.Contas':>14} {'Transf.':>14} {'Diff':>12}")
print("-" * 82)

tot_ext = tot_conf = tot_pag = tot_trans = 0.0
diff_days = 0

for d in sorted(daily.keys(), key=lambda x: x.split("/")[::-1]):
    dd = daily[d]
    diff = dd["extrato"] - (dd["conf"] + dd["pag"] + dd["trans"])
    tot_ext += dd["extrato"]
    tot_conf += dd["conf"]
    tot_pag += dd["pag"]
    tot_trans += dd["trans"]
    flag = " *** DIFF" if abs(diff) > 0.01 else ""
    print(f"{d:<12} {dd['extrato']:>12,.2f} {dd['conf']:>14,.2f} {dd['pag']:>14,.2f} {dd['trans']:>14,.2f} {diff:>12,.2f}{flag}")
    if abs(diff) > 0.01:
        diff_days += 1

total_diff = tot_ext - (tot_conf + tot_pag + tot_trans)
print("-" * 82)
print(f"{'TOTAL':<12} {tot_ext:>12,.2f} {tot_conf:>14,.2f} {tot_pag:>14,.2f} {tot_trans:>14,.2f} {total_diff:>12,.2f}")

print(f"""
RESUMO FLUXO DE CAIXA:
  Saldo inicial:     R$ {initial_balance:>12,.2f}
  (+) Créditos:      R$ {sum(l['amount'] for l in extrato_lines if l['amount'] > 0):>12,.2f}
  (-) Débitos:       R$ {sum(l['amount'] for l in extrato_lines if l['amount'] < 0):>12,.2f}
  = Saldo final:     R$ {final_balance:>12,.2f}
  Variação:          R$ {final_balance - initial_balance:>12,.2f}

  Confirmados:       R$ {tot_conf:>12,.2f}  ({len(confirmados)} linhas)
  Pag. Contas:       R$ {tot_pag:>12,.2f}  ({len(pagamento_contas)} linhas)
  Transferências:    R$ {tot_trans:>12,.2f}  ({len(transferencias)} linhas)
  Diferença total:   R$ {total_diff:>12,.2f}  ({diff_days} dias com diferença)
""")

if abs(total_diff) < 0.01:
    print("✓ RECONCILIAÇÃO OK — Diff = R$ 0,00 (todos os dias batem)")
else:
    print(f"✗ RECONCILIAÇÃO COM DIFERENÇA — R$ {total_diff:,.2f}")
    # Show days with diff
    print("\nDias com diferença:")
    for d in sorted(daily.keys(), key=lambda x: x.split("/")[::-1]):
        dd = daily[d]
        diff = dd["extrato"] - (dd["conf"] + dd["pag"] + dd["trans"])
        if abs(diff) > 0.01:
            print(f"  {d}: extrato={dd['extrato']:,.2f} classificado={dd['conf']+dd['pag']+dd['trans']:,.2f} diff={diff:,.2f}")
