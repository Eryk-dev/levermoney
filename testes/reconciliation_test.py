"""
Reconciliation Test: Extrato vs New API (Supabase data)

Compares the February 2026 account statement (extrato) for 141air
against what the new API has in Supabase (payments + mp_expenses).

Goal: Identify what's being posted correctly and what's missing.
"""

from collections import defaultdict
import csv
import io

# ============================================================
# 1. PARSE EXTRATO
# ============================================================

EXTRATO_FILE = "/Volumes/SSD Eryk/financeiro v2/lever money claude v3/api nova/testes/extratos 141air/account_statement-6e669823-8bc7-4ad9-b9a3-95703e5e6b04.csv"

def parse_amount(s):
    """Parse Brazilian number format: 1.234,56 -> 1234.56"""
    return float(s.replace(".", "").replace(",", "."))

extrato_lines = []
with open(EXTRATO_FILE, "r", encoding="utf-8") as f:
    # Skip header lines (INITIAL_BALANCE row + empty line + column header)
    lines = f.readlines()
    # Find the data start (after RELEASE_DATE header)
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
            "type": parts[1],
            "ref_id": parts[2],
            "amount": parse_amount(parts[3]),
            "balance": parse_amount(parts[4]),
        })

# ============================================================
# 2. SUPABASE DATA (hardcoded from queries above)
# ============================================================

# Payments in Supabase with money_release_date within extrato period (Feb 1-17)
# Format: {ml_payment_id: {amount, net_amount, money_release_date, ml_status}}
payments_db = {
    144522992246: {"amount": 18.90, "net": 10.38, "release": "2026-02-02", "status": "refunded"},
    144575400964: {"amount": 358.46, "net": 303.08, "release": "2026-02-03", "status": "refunded"},
    144745203021: {"amount": 19.90, "net": 11.26, "release": "2026-02-08", "status": "refunded"},
    144499689691: {"amount": 76.90, "net": 60.92, "release": "2026-02-09", "status": "refunded"},
    145508379302: {"amount": 76.90, "net": 60.92, "release": "2026-02-09", "status": "refunded"},
    145533986422: {"amount": 523.90, "net": 437.58, "release": "2026-02-09", "status": "refunded"},
    143695465493: {"amount": 94.90, "net": 71.54, "release": "2026-02-10", "status": "approved"},
    143699005939: {"amount": 115.80, "net": 87.94, "release": "2026-02-10", "status": "charged_back"},
    144847500705: {"amount": 60.90, "net": 46.84, "release": "2026-02-10", "status": "refunded"},
    143684596337: {"amount": 309.00, "net": 248.47, "release": "2026-02-11", "status": "approved"},
    144512181010: {"amount": 284.90, "net": 227.26, "release": "2026-02-11", "status": "approved"},
    144760261462: {"amount": 402.90, "net": 334.60, "release": "2026-02-11", "status": "refunded"},
    143860279383: {"amount": 149.19, "net": 112.48, "release": "2026-02-12", "status": "approved"},
    144370799868: {"amount": 284.74, "net": 235.85, "release": "2026-02-12", "status": "approved"},
    144467225064: {"amount": 293.96, "net": 244.32, "release": "2026-02-12", "status": "approved"},
    144474946344: {"amount": 27.55, "net": 18.61, "release": "2026-02-12", "status": "approved"},
    144491410542: {"amount": 101.27, "net": 75.81, "release": "2026-02-12", "status": "approved"},
    144554804812: {"amount": 284.90, "net": 227.26, "release": "2026-02-12", "status": "approved"},
    144660431580: {"amount": 358.46, "net": 301.58, "release": "2026-02-12", "status": "approved"},
    144775563918: {"amount": 99.00, "net": 75.15, "release": "2026-02-12", "status": "approved"},
    143708806971: {"amount": 279.90, "net": 222.86, "release": "2026-02-13", "status": "approved"},
    143794551503: {"amount": 36.05, "net": 26.16, "release": "2026-02-13", "status": "approved"},
    143837265939: {"amount": 411.80, "net": 319.48, "release": "2026-02-13", "status": "approved"},
    143856280755: {"amount": 238.67, "net": 193.90, "release": "2026-02-13", "status": "approved"},
    143942313087: {"amount": 273.90, "net": 203.89, "release": "2026-02-13", "status": "approved"},
    144008175803: {"amount": 118.90, "net": 90.67, "release": "2026-02-13", "status": "approved"},
    144012397443: {"amount": 111.39, "net": 79.46, "release": "2026-02-13", "status": "approved"},
    144060881815: {"amount": 303.17, "net": 247.76, "release": "2026-02-13", "status": "approved"},
    144404040084: {"amount": 50.59, "net": 38.79, "release": "2026-02-13", "status": "approved"},
    144500605642: {"amount": 142.40, "net": 109.35, "release": "2026-02-13", "status": "in_mediation"},
    144506403470: {"amount": 246.90, "net": 184.98, "release": "2026-02-13", "status": "approved"},
    144532246692: {"amount": 239.90, "net": 191.16, "release": "2026-02-13", "status": "approved"},
    144575314490: {"amount": 594.90, "net": 496.56, "release": "2026-02-13", "status": "approved"},
    144647797738: {"amount": 404.90, "net": 332.86, "release": "2026-02-13", "status": "approved"},
    144655042578: {"amount": 180.90, "net": 141.24, "release": "2026-02-13", "status": "approved"},
    143793233547: {"amount": 255.90, "net": 202.74, "release": "2026-02-14", "status": "approved"},
    143814720883: {"amount": 710.90, "net": 566.60, "release": "2026-02-14", "status": "approved"},
    143822888297: {"amount": 874.90, "net": 697.72, "release": "2026-02-14", "status": "approved"},
    143943579575: {"amount": 157.90, "net": 111.76, "release": "2026-02-14", "status": "approved"},
    143975380415: {"amount": 27.02, "net": 18.23, "release": "2026-02-14", "status": "approved"},
    144160334853: {"amount": 284.74, "net": 235.85, "release": "2026-02-14", "status": "approved"},
    144550579018: {"amount": 276.35, "net": 228.29, "release": "2026-02-14", "status": "approved"},
    144614775184: {"amount": 628.90, "net": 529.98, "release": "2026-02-14", "status": "approved"},
    144723352730: {"amount": 129.90, "net": 91.86, "release": "2026-02-14", "status": "approved"},
    144756525944: {"amount": 86.99, "net": 63.68, "release": "2026-02-14", "status": "approved"},
    144781704800: {"amount": 122.90, "net": 92.19, "release": "2026-02-14", "status": "approved"},
    142596223717: {"amount": 297.80, "net": 271.00, "release": "2026-02-15", "status": "approved"},
    143277354070: {"amount": 25.00, "net": 23.88, "release": "2026-02-15", "status": "approved"},
    143939314942: {"amount": 141.64, "net": 100.40, "release": "2026-02-15", "status": "approved"},
    143988623955: {"amount": 247.88, "net": 202.35, "release": "2026-02-15", "status": "approved"},
    144141627247: {"amount": 118.90, "net": 90.67, "release": "2026-02-15", "status": "approved"},
    144197811337: {"amount": 43.50, "net": 20.19, "release": "2026-02-15", "status": "approved"},
    144197841299: {"amount": 43.49, "net": 43.49, "release": "2026-02-15", "status": "approved"},
    144297217627: {"amount": 238.67, "net": 193.96, "release": "2026-02-15", "status": "approved"},
    144359445042: {"amount": 56.90, "net": 40.76, "release": "2026-02-15", "status": "approved"},
    144553021998: {"amount": 35.90, "net": 25.09, "release": "2026-02-15", "status": "approved"},
    144574485192: {"amount": 276.35, "net": 228.29, "release": "2026-02-15", "status": "approved"},
    144612647582: {"amount": 1130.90, "net": 950.74, "release": "2026-02-15", "status": "approved"},
    144846013476: {"amount": 1957.90, "net": 1694.50, "release": "2026-02-15", "status": "approved"},
    144899316468: {"amount": 160.90, "net": 122.29, "release": "2026-02-15", "status": "approved"},
    144507376097: {"amount": 134.90, "net": 101.55, "release": "2026-02-16", "status": "refunded"},
    144618092842: {"amount": 2328.90, "net": 2004.98, "release": "2026-02-16", "status": "approved"},
    144663343534: {"amount": 276.35, "net": 228.29, "release": "2026-02-16", "status": "approved"},
    144905265884: {"amount": 111.39, "net": 79.46, "release": "2026-02-16", "status": "refunded"},
    144931393792: {"amount": 289.00, "net": 230.87, "release": "2026-02-16", "status": "approved"},
    143999312569: {"amount": 284.74, "net": 235.85, "release": "2026-02-17", "status": "approved"},
    144258085137: {"amount": 257.10, "net": 207.25, "release": "2026-02-17", "status": "approved"},
    144817865886: {"amount": 299.00, "net": 239.67, "release": "2026-02-17", "status": "approved"},
    145144675862: {"amount": 153.90, "net": 117.48, "release": "2026-02-17", "status": "approved"},
    145217109760: {"amount": 273.90, "net": 203.89, "release": "2026-02-17", "status": "approved"},
    145999593422: {"amount": 108.28, "net": 84.60, "release": "2026-02-17", "status": "refunded"},
}

# mp_expenses in Supabase
mp_expenses_db = {
    145795817215: {"amount": 1474.59, "type": "bill_payment", "desc": "Boleto - Itaú Unibanco S.A."},
    146639898580: {"amount": 377.26, "type": "bill_payment", "desc": "Boleto - Itaú Unibanco S.A."},
}

# ============================================================
# 3. CLASSIFY EACH EXTRATO LINE
# ============================================================

def classify_extrato_line(line):
    """Classify an extrato line and check if it's covered by the new API."""
    t = line["type"]
    ref = line["ref_id"]
    ref_int = int(ref) if ref.isdigit() else None

    # Check if reference matches a payment in Supabase
    in_payments = ref_int in payments_db if ref_int else False
    in_expenses = ref_int in mp_expenses_db if ref_int else False

    # Classify the transaction type
    if "Liberação de dinheiro cancelada" in t:
        return "CANCELLED_RELEASE", in_payments, in_expenses
    elif "Liberação de dinheiro" in t:
        return "RELEASE", in_payments, in_expenses
    elif "Pagamento com Código QR Pix" in t or "Pagamento com QR Pix" in t:
        # QR PIX could be sale or bill payment
        if "RECEITA FEDERAL" in t or "MINISTERIO DA FAZENDA" in t:
            return "DARF", False, in_expenses
        else:
            return "PIX_SALE", in_payments, in_expenses
    elif "Dinheiro recebido Pagamento pelo Programa de Proteção" in t:
        return "INSURANCE_PAYMENT", in_payments, in_expenses
    elif "Dinheiro recebido" in t:
        return "MONEY_RECEIVED", in_payments, in_expenses
    elif "Dinheiro retido" in t:
        return "HELD_FUNDS", in_payments, in_expenses
    elif "Débito por dívida Reclamações" in t:
        return "CHARGEBACK_DEBIT", in_payments, in_expenses
    elif "Débito por dívida Envio" in t:
        return "SHIPPING_DEBIT", in_payments, in_expenses
    elif "Reembolso Reclamações" in t:
        return "REFUND_CLAIM_REVERSAL", in_payments, in_expenses
    elif "Reembolso Envío cancelado" in t:
        return "REFUND_SHIPPING_CANCEL", in_payments, in_expenses
    elif "Reembolso Compra garantida" in t:
        return "REFUND_PURCHASE_GUARANTEE", in_payments, in_expenses
    elif "Reembolso" in t:
        return "REFUND_OTHER", in_payments, in_expenses
    elif "Transferência Pix enviada" in t:
        return "PIX_TRANSFER_OUT", False, in_expenses
    elif "Pix enviado" in t:
        return "PIX_SENT", False, in_expenses
    elif "Pagamento de conta" in t:
        if "DARF" in t:
            return "DARF", False, in_expenses
        else:
            return "BILL_PAYMENT", False, in_expenses
    elif "Pagamento Claude" in t or "Pagamento Supabase" in t:
        return "SUBSCRIPTION", False, in_expenses
    elif "Dinheiro reservado Renda" in t:
        return "SAVINGS_POT", False, False
    elif "Bônus por envio" in t:
        return "SHIPPING_BONUS", False, in_expenses
    elif "Entrada de dinheiro" in t:
        return "FEE_REFUND_ENTRY", in_payments, in_expenses
    else:
        return "UNKNOWN", in_payments, in_expenses

# ============================================================
# 4. BUILD RECONCILIATION REPORT
# ============================================================

# Group by date
daily = defaultdict(lambda: {
    "extrato_lines": [],
    "extrato_total": 0.0,
    "api_covered_total": 0.0,
    "not_covered_total": 0.0,
    "api_covered_lines": [],
    "not_covered_lines": [],
})

type_summary = defaultdict(lambda: {"count": 0, "total": 0.0, "covered": 0, "not_covered": 0})

for line in extrato_lines:
    date = line["date"]
    category, in_pay, in_exp = classify_extrato_line(line)
    covered = in_pay or in_exp

    d = daily[date]
    d["extrato_lines"].append(line)
    d["extrato_total"] += line["amount"]

    line_info = {
        "ref_id": line["ref_id"],
        "type": line["type"][:80],
        "amount": line["amount"],
        "category": category,
        "in_payments": in_pay,
        "in_expenses": in_exp,
    }

    if covered:
        d["api_covered_total"] += line["amount"]
        d["api_covered_lines"].append(line_info)
    else:
        d["not_covered_total"] += line["amount"]
        d["not_covered_lines"].append(line_info)

    ts = type_summary[category]
    ts["count"] += 1
    ts["total"] += line["amount"]
    if covered:
        ts["covered"] += 1
    else:
        ts["not_covered"] += 1

# ============================================================
# 5. PRINT REPORT
# ============================================================

print("=" * 100)
print("RECONCILIATION REPORT: EXTRATO vs NEW API (141air, Feb 2026)")
print("=" * 100)

# Summary by transaction type
print("\n" + "=" * 100)
print("SUMMARY BY TRANSACTION TYPE")
print("=" * 100)
print(f"{'Category':<30} {'Count':>6} {'Total R$':>12} {'Covered':>8} {'Missing':>8}")
print("-" * 100)

sorted_types = sorted(type_summary.items(), key=lambda x: abs(x[1]["total"]), reverse=True)
for cat, info in sorted_types:
    print(f"{cat:<30} {info['count']:>6} {info['total']:>12,.2f} {info['covered']:>8} {info['not_covered']:>8}")

total_covered = sum(d["api_covered_total"] for d in daily.values())
total_not_covered = sum(d["not_covered_total"] for d in daily.values())
total_extrato = sum(d["extrato_total"] for d in daily.values())

print("-" * 100)
print(f"{'TOTAL':<30} {sum(t['count'] for t in type_summary.values()):>6} {total_extrato:>12,.2f} "
      f"{'':>8} {'':>8}")

# What is covered vs not
print("\n" + "=" * 100)
print("COVERAGE ANALYSIS")
print("=" * 100)

# Release lines (the main baixa candidates)
release_covered = 0
release_total = 0
release_not_covered_list = []

for line in extrato_lines:
    cat, in_pay, in_exp = classify_extrato_line(line)
    if cat == "RELEASE":
        release_total += 1
        if in_pay:
            release_covered += 1
        else:
            release_not_covered_list.append(line)

print(f"\nLiberação de dinheiro: {release_covered}/{release_total} matched in payments table")
if release_not_covered_list:
    print("  UNMATCHED releases:")
    for l in release_not_covered_list:
        print(f"    {l['date']} | ref={l['ref_id']} | R${l['amount']:,.2f}")

# PIX sales
pix_covered = 0
pix_total = 0
pix_not_covered_list = []

for line in extrato_lines:
    cat, in_pay, in_exp = classify_extrato_line(line)
    if cat == "PIX_SALE":
        pix_total += 1
        if in_pay:
            pix_covered += 1
        else:
            pix_not_covered_list.append(line)

print(f"\nPagamento QR Pix (sales): {pix_covered}/{pix_total} matched in payments table")
if pix_not_covered_list:
    print("  UNMATCHED PIX sales:")
    for l in pix_not_covered_list:
        print(f"    {l['date']} | ref={l['ref_id']} | R${l['amount']:,.2f} | {l['type'][:60]}")

# Money received
mr_covered = 0
mr_total = 0
mr_not_covered_list = []

for line in extrato_lines:
    cat, in_pay, in_exp = classify_extrato_line(line)
    if cat == "MONEY_RECEIVED":
        mr_total += 1
        if in_pay:
            mr_covered += 1
        else:
            mr_not_covered_list.append(line)

print(f"\nDinheiro recebido: {mr_covered}/{mr_total} matched in payments table")
if mr_not_covered_list:
    print("  UNMATCHED money received:")
    for l in mr_not_covered_list:
        print(f"    {l['date']} | ref={l['ref_id']} | R${l['amount']:,.2f}")

# ============================================================
# 6. NON-SALE MOVEMENTS (not covered by API at all)
# ============================================================

print("\n" + "=" * 100)
print("NON-SALE MOVEMENTS NOT COVERED BY NEW API")
print("=" * 100)

non_sale_categories = [
    "DARF", "BILL_PAYMENT", "SUBSCRIPTION", "PIX_TRANSFER_OUT", "PIX_SENT",
    "SAVINGS_POT", "SHIPPING_BONUS", "INSURANCE_PAYMENT",
    "HELD_FUNDS", "CHARGEBACK_DEBIT", "SHIPPING_DEBIT",
    "REFUND_CLAIM_REVERSAL", "REFUND_SHIPPING_CANCEL", "REFUND_OTHER",
    "REFUND_PURCHASE_GUARANTEE", "FEE_REFUND_ENTRY", "CANCELLED_RELEASE",
]

for cat in non_sale_categories:
    if cat not in type_summary:
        continue
    info = type_summary[cat]
    if info["not_covered"] == 0:
        continue

    print(f"\n--- {cat} ({info['not_covered']} lines, R${info['total']:,.2f}) ---")
    for line in extrato_lines:
        c, in_p, in_e = classify_extrato_line(line)
        if c == cat and not in_p and not in_e:
            print(f"  {line['date']} | ref={line['ref_id']:>15} | R${line['amount']:>10,.2f} | {line['type'][:60]}")

# ============================================================
# 7. DAILY RECONCILIATION TABLE
# ============================================================

print("\n" + "=" * 100)
print("DAILY RECONCILIATION: EXTRATO vs API BAIXAS")
print("=" * 100)
print(f"{'Date':<12} {'Extrato':>12} {'API Sales':>12} {'Non-Sale':>12} {'Gap':>12} {'Lines':>6}")
print("-" * 80)

# For daily reconciliation, calculate what the API would baixa (net of releases matching payments)
for date in sorted(daily.keys()):
    d = daily[date]

    # API baixa = sum of net amounts for payments released on this day
    api_baixa = 0.0
    for line in d["extrato_lines"]:
        cat, in_pay, _ = classify_extrato_line(line)
        if in_pay and cat in ("RELEASE", "PIX_SALE", "MONEY_RECEIVED"):
            api_baixa += line["amount"]

    # Non-sale = all lines NOT matched to payments
    non_sale = d["not_covered_total"]

    gap = d["extrato_total"] - (api_baixa + non_sale)

    print(f"{date:<12} {d['extrato_total']:>12,.2f} {api_baixa:>12,.2f} {non_sale:>12,.2f} {gap:>12,.2f} {len(d['extrato_lines']):>6}")

print("-" * 80)
print(f"{'TOTAL':<12} {total_extrato:>12,.2f} {total_covered:>12,.2f} {total_not_covered:>12,.2f} "
      f"{total_extrato - (total_covered + total_not_covered):>12,.2f}")

# ============================================================
# 8. SPECIFIC PROBLEM: Refund/chargeback/mediation flows
# ============================================================

print("\n" + "=" * 100)
print("REFUND/CHARGEBACK/MEDIATION FLOWS (cash impact NOT captured by API)")
print("=" * 100)

# Group refund-related movements by reference_id
refund_refs = defaultdict(list)
for line in extrato_lines:
    cat, _, _ = classify_extrato_line(line)
    if cat in ("CHARGEBACK_DEBIT", "SHIPPING_DEBIT", "REFUND_CLAIM_REVERSAL",
               "REFUND_SHIPPING_CANCEL", "REFUND_OTHER", "REFUND_PURCHASE_GUARANTEE",
               "HELD_FUNDS", "FEE_REFUND_ENTRY", "CANCELLED_RELEASE"):
        refund_refs[line["ref_id"]].append({
            "date": line["date"],
            "type": cat,
            "amount": line["amount"],
            "desc": line["type"][:80],
        })

# Show net impact per reference
print(f"\n{'Ref ID':>15} {'Net Impact':>12} {'In DB?':>8} {'Lines':>6}")
print("-" * 60)
total_refund_impact = 0.0
for ref_id, movements in sorted(refund_refs.items()):
    net = sum(m["amount"] for m in movements)
    ref_int = int(ref_id) if ref_id.isdigit() else None
    in_db = "YES" if (ref_int and ref_int in payments_db) else "NO"
    total_refund_impact += net
    if abs(net) >= 0.01:  # Only show non-zero net impacts
        print(f"{ref_id:>15} {net:>12,.2f} {in_db:>8} {len(movements):>6}")

print("-" * 60)
print(f"{'TOTAL':>15} {total_refund_impact:>12,.2f}")

# ============================================================
# 9. BILL PAYMENTS COVERAGE
# ============================================================

print("\n" + "=" * 100)
print("BILL PAYMENTS / DARF / SUBSCRIPTIONS COVERAGE")
print("=" * 100)

bill_cats = ["DARF", "BILL_PAYMENT", "SUBSCRIPTION"]
for cat in bill_cats:
    if cat not in type_summary:
        continue
    info = type_summary[cat]
    print(f"\n{cat}: {info['count']} lines, total R${info['total']:,.2f}")
    print(f"  Covered by mp_expenses: {info['covered']}")
    print(f"  NOT covered: {info['not_covered']}")

# ============================================================
# 10. FINAL DIAGNOSIS
# ============================================================

print("\n" + "=" * 100)
print("FINAL DIAGNOSIS")
print("=" * 100)

print(f"""
Extrato period: Feb 01-17, 2026
Initial balance: R$1,090.40
Final balance: R$2,862.73
Total credits: R$51,335.76
Total debits: R$-49,563.43

What the NEW API covers (in Supabase):
  - Payments (receita + comissão + frete): {len(payments_db)} payments
  - MP expenses (bill payments): {len(mp_expenses_db)} expenses

What the NEW API is MISSING from the extrato:
""")

missing_categories = {}
for cat, info in sorted_types:
    if info["not_covered"] > 0:
        missing_categories[cat] = info

for cat, info in missing_categories.items():
    lines_not_covered = [l for l in extrato_lines if classify_extrato_line(l)[0] == cat and not classify_extrato_line(l)[1] and not classify_extrato_line(l)[2]]
    total_missing = sum(l["amount"] for l in lines_not_covered)
    if abs(total_missing) >= 0.01:
        print(f"  {cat}: {info['not_covered']} lines, R${total_missing:,.2f}")

print(f"""
KEY FINDINGS:
1. Sale releases (Liberação/PIX/Dinheiro recebido): How many match payments?
2. Refund/chargeback flows: These create multiple extrato lines per payment but
   the API only handles the original payment status change, not the individual
   cash flow lines (held funds, debits, refund reversals).
3. Non-sale movements completely missing from API:
   - DARF payments (R$125 each, ~30 on Feb 9)
   - Savings pot (Dinheiro reservado Renda)
   - Shipping bonuses
   - Insurance payments
   - PIX transfers out
   - Some bill payments not in mp_expenses
""")
