#!/usr/bin/env python3
"""Fase 0 — O JUIZ de reconciliacao de caixa (jan/2026).

Standalone, stdlib only. Nao precisa de Supabase/env/CA API.
Le os extratos CSV reais (testes/extratos/) + cache de payments (testes/cache_jan2026/).

Responde 3 perguntas:

  (A) ANCORA: o extrato fecha sozinho?  INITIAL + sum(net) == FINAL ?
      e o saldo corrido (PARTIAL_BALANCE) bate linha a linha?
      -> prova que o extrato e uma verdade confiavel pra ancorar.

  (B) BUCKETS: classificando cada linha COMO O SISTEMA FAZ
      (mesmas regras de extrato_ingester.EXTRATO_CLASSIFICATION_RULES),
      quanto do movimento cai em: vendas (coberto pelo processor),
      non-venda classificado, ou OTHER (cauda manual) — e quanto e BUG conhecido.

  (C) RECON DE VENDAS (so 141air, tem cache): para cada "Liberacao de dinheiro"
      do extrato, compara o NET real liberado vs o NET que o processor calcularia
      (amount - comissao - frete). A soma das diferencas = exposicao de taxa oculta.
"""
import csv
import json
import os
import re
import unicodedata
from collections import defaultdict

BASE = os.path.dirname(os.path.abspath(__file__))
EXTRATO_DIR = os.path.join(BASE, "extratos")
CACHE_DIR = os.path.join(BASE, "cache_jan2026")

# Mapa: arquivo de extrato jan -> slug do cache (None = sem cache)
SELLERS = [
    ("extrato janeiro 141Air.csv", "141air"),
    ("extrato janeiro netair.csv", None),
    ("extrato janeiro netparts.csv", None),
    ("extrato janeiro Easyutilidades.csv", None),
]

# Importa as regras REAIS do app (sem copia local — assim o juiz testa o codigo de verdade).
import sys as _sys, os as _os
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
from app.services.extrato_ingester import EXTRATO_CLASSIFICATION_RULES as RULES


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", s.lower()).strip()


def parse_br(raw: str) -> float:
    raw = (raw or "").strip().replace(".", "").replace(",", ".")
    if not raw:
        return 0.0
    try:
        return float(raw)
    except ValueError:
        return 0.0


def classify(tx_type: str):
    n = norm(tx_type)
    # venda: liberacao de dinheiro (nao cancelada) e coberta pelo processor
    if "liberacao de dinheiro" in n and "cancelada" not in n:
        return "__SALE__", None, None, "liberacao de dinheiro"
    for pat, etype, direction, code in RULES:
        if pat in n:
            return etype, direction, code, pat
    return "__OTHER__", None, None, None


def load_extrato(path):
    """Retorna (header_dict, [linhas])."""
    header = {}
    rows = []
    with open(path, encoding="utf-8") as f:
        lines = f.read().splitlines()
    i = 0
    # bloco header INITIAL_BALANCE;CREDITS;DEBITS;FINAL_BALANCE
    while i < len(lines):
        if lines[i].startswith("INITIAL_BALANCE"):
            vals = lines[i + 1].split(";")
            header = {
                "initial": parse_br(vals[0]),
                "credits": parse_br(vals[1]) if len(vals) > 1 else 0.0,
                "debits": parse_br(vals[2]) if len(vals) > 2 else 0.0,
                "final": parse_br(vals[3]) if len(vals) > 3 else 0.0,
            }
            i += 2
            continue
        if lines[i].startswith("RELEASE_DATE"):
            i += 1
            while i < len(lines):
                ln = lines[i].strip()
                if ln:
                    parts = ln.split(";")
                    if len(parts) >= 5:
                        rows.append({
                            "date": parts[0].strip(),
                            "type": parts[1].strip(),
                            "ref": parts[2].strip(),
                            "net": parse_br(parts[3]),
                            "balance": parse_br(parts[4]),
                        })
                i += 1
            break
        i += 1
    return header, rows


# Padroes de BUG conhecidos (linha cai em lugar errado / sinal errado)
def bug_flags(tx_type: str, net: float, etype: str, direction: str):
    """Outcome-aware: flaga so se a linha REALMENTE foi mal tratada (pelo resultado, nao pelo texto)."""
    n = norm(tx_type)
    flags = []
    # pix recebido: entrada real ainda caindo em OTHER
    if "pix recebido" in n and etype == "__OTHER__":
        flags.append("pix_recebido_sem_regra")
    # reembolso de boleto: ainda SKIPADO (etype None) = entrada perdida
    if "reembolso" in n and "pagamento de conta" in n and etype is None:
        flags.append("reembolso_conta_skip_indevido")
    # sinal: reversao ("...cancelad") classificada como income (entrada) com net negativo
    if "cancelad" in n and direction == "income" and net < 0:
        flags.append("cancelamento_sinal")
    # compra ML PT ainda em OTHER
    if "compra mercado livre" in n and etype == "__OTHER__":
        flags.append("compra_ml_pt_other")
    return flags


def fmt(v):
    return f"{v:>14,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def run_anchor(header, rows):
    sum_net = sum(r["net"] for r in rows)
    expected_final = header["initial"] + sum_net
    anchor_diff = expected_final - header["final"]
    # saldo corrido
    bal = header["initial"]
    max_drift = 0.0
    drift_lines = 0
    for r in rows:
        bal += r["net"]
        d = bal - r["balance"]
        if abs(d) > 0.01:
            drift_lines += 1
            max_drift = max(max_drift, abs(d))
    return sum_net, expected_final, anchor_diff, drift_lines, max_drift


def run_buckets(rows):
    buckets = defaultdict(lambda: {"count": 0, "sum": 0.0, "abs": 0.0})
    bugs = defaultdict(lambda: {"count": 0, "sum": 0.0})
    for r in rows:
        etype, direction, code, pat = classify(r["type"])
        if etype == "__SALE__":
            key = "VENDAS (liberacao, coberto processor)"
        elif etype is None:
            key = "SKIP (coberto API/processor)"
        elif etype == "__OTHER__":
            key = "OTHER (cauda manual / sem regra)"
        else:
            tag = "income" if direction == "income" else ("transfer" if direction == "transfer" else "expense")
            cov = "auto" if code else "pending_review"
            key = f"classificado:{tag}:{cov}"
        b = buckets[key]
        b["count"] += 1
        b["sum"] += r["net"]
        b["abs"] += abs(r["net"])
        for fl in bug_flags(r["type"], r["net"], etype, direction):
            bugs[fl]["count"] += 1
            bugs[fl]["sum"] += r["net"]
    return buckets, bugs


def run_sales_recon(rows, slug):
    """141air: compara net liberado (extrato) vs net calculado (processor) por ref_id."""
    cache_path = os.path.join(CACHE_DIR, f"{slug}_payments.json")
    if not os.path.exists(cache_path):
        return None
    raw = json.load(open(cache_path))
    plist = raw.get("payments", raw) if isinstance(raw, dict) else raw
    by_id = {}
    for p in plist:
        if not isinstance(p, dict):
            continue
        if p.get("id") is not None:
            by_id[str(p.get("id"))] = p

    def proc_net(p):
        amount = float(p.get("transaction_amount") or 0)
        shipping_amount = float(p.get("shipping_amount") or 0)
        comissao = 0.0
        frete_collector = 0.0
        for ch in (p.get("charges_details") or []):
            accts = ch.get("accounts") or {}
            frm = accts.get("from")
            ctype = ch.get("type")
            name = ch.get("name") or ""
            orig = (ch.get("amounts") or {}).get("original") or 0
            try:
                orig = float(orig)
            except (TypeError, ValueError):
                orig = 0.0
            if frm != "collector":
                continue
            if ctype == "fee" and name != "financing_fee":
                comissao += orig
            elif ctype == "shipping":
                frete_collector += orig
        frete_seller = max(0.0, frete_collector - shipping_amount)
        return amount - comissao - frete_seller

    matched = 0
    unmatched = 0
    sum_extrato = 0.0
    sum_proc = 0.0
    sum_absdiff = 0.0
    worst = []
    for r in rows:
        if "liberacao de dinheiro" not in norm(r["type"]) or "cancelada" in norm(r["type"]):
            continue
        ref = str(r["ref"])
        p = by_id.get(ref)
        sum_extrato += r["net"]
        if not p:
            unmatched += 1
            continue
        matched += 1
        pn = proc_net(p)
        sum_proc += pn
        diff = r["net"] - pn
        sum_absdiff += abs(diff)
        if abs(diff) > 0.01:
            worst.append((abs(diff), ref, r["net"], pn, p.get("status"), p.get("status_detail")))
    worst.sort(reverse=True)
    return {
        "matched": matched, "unmatched": unmatched,
        "sum_extrato": sum_extrato, "sum_proc": sum_proc,
        "sum_absdiff": sum_absdiff, "worst": worst[:10],
        "net_diff": sum_extrato - sum_proc,
    }


def main():
    print("=" * 90)
    print("FASE 0 — JUIZ DE RECONCILIACAO DE CAIXA — jan/2026")
    print("=" * 90)
    for fname, slug in SELLERS:
        path = os.path.join(EXTRATO_DIR, fname)
        if not os.path.exists(path):
            print(f"\n!! faltando: {fname}")
            continue
        header, rows = load_extrato(path)
        print(f"\n{'#'*90}\n# {fname}   (linhas={len(rows)}, cache={'sim' if slug else 'NAO'})\n{'#'*90}")

        # (A) ANCORA
        sum_net, exp_final, anchor_diff, drift_lines, max_drift = run_anchor(header, rows)
        print("\n[A] ANCORA (extrato fecha sozinho?)")
        print(f"    INITIAL_BALANCE      = {fmt(header['initial'])}")
        print(f"    + sum(net) linhas    = {fmt(sum_net)}")
        print(f"    = esperado final     = {fmt(exp_final)}")
        print(f"    FINAL_BALANCE (real) = {fmt(header['final'])}")
        print(f"    >> DIFF ANCORA       = {fmt(anchor_diff)}   {'OK ✓' if abs(anchor_diff)<0.01 else 'DIVERGE ✗'}")
        print(f"    saldo corrido: {drift_lines} linha(s) com drift, max={fmt(max_drift)}")

        # (B) BUCKETS
        buckets, bugs = run_buckets(rows)
        total_abs = sum(b["abs"] for b in buckets.values()) or 1.0
        print("\n[B] BUCKETS (movimento por como o sistema classifica)")
        print(f"    {'bucket':<46}{'qtd':>6}{'soma_net':>16}{'%mov(abs)':>11}")
        for key in sorted(buckets, key=lambda k: -buckets[k]["abs"]):
            b = buckets[key]
            print(f"    {key:<46}{b['count']:>6}{fmt(b['sum'])}{100*b['abs']/total_abs:>10.1f}%")
        print(f"    movimento total (Σ|net|) = {fmt(total_abs)}")

        if bugs:
            print("\n    BUGS detectados (linhas mal tratadas):")
            for fl, b in sorted(bugs.items(), key=lambda kv: -abs(kv[1]['sum'])):
                print(f"      - {fl:<34}{b['count']:>5} linha(s)  net={fmt(b['sum'])}")
        else:
            print("\n    BUGS detectados: nenhum nas amostras")

        # (C) RECON DE VENDAS
        if slug:
            rec = run_sales_recon(rows, slug)
            if rec:
                print("\n[C] RECON DE VENDAS (extrato liberado vs processor calculado)")
                print(f"    liberacoes casadas c/ cache : {rec['matched']}  | sem cache: {rec['unmatched']}")
                print(f"    Σ net liberado (extrato)    = {fmt(rec['sum_extrato'])}")
                print(f"    Σ net calculado (processor) = {fmt(rec['sum_proc'])}")
                print(f"    >> NET_DIFF (taxa oculta)   = {fmt(rec['net_diff'])}")
                print(f"    Σ |diff| por payment        = {fmt(rec['sum_absdiff'])}  (erro bruto somado)")
                if rec["worst"]:
                    print("    piores divergencias (|diff|, ref, extrato, processor, status):")
                    for d, ref, ext, pn, st, sd in rec["worst"]:
                        print(f"      {fmt(d)}  ref={ref:<14} ext={fmt(ext)} proc={fmt(pn)} [{st}/{sd}]")
    print("\n" + "=" * 90)


if __name__ == "__main__":
    main()
