"""Diagnóstico de causa-raiz do resíduo de venda.

Para cada ref que o processor TOCOU e que aparece no extrato, classifica a divergência
(ext_total_ref - ca_net_ref) em buckets de CAUSA, e soma R$ por bucket. Mostra quanto é
bug eliminável (refund parcial, taxa oculta) vs estrutural (boundary/timing).

Uso: python3 -m testes.harness.diag 141air
"""
import asyncio
import json
import os
import sys
import importlib.util
from collections import defaultdict
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from testes.harness.dryrun import run_seller_month, SIGN

_jp = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "judge_caixa_jan2026.py")
_s = importlib.util.spec_from_file_location("judge", _jp)
judge = importlib.util.module_from_spec(_s); _s.loader.exec_module(judge)

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MONTHS = ["jan", "fev", "mar", "abr", "mai"]
MDIR = {"jan": "cache_jan2026", "fev": "cache_fev2026", "mar": "cache_mar2026",
        "abr": "cache_abr2026", "mai": "cache_mai2026"}
EMAP = {"janeiro", "fevereiro", "marco", "abril", "maio"}
EXTRATO_MAP = {
    ("141air", "jan"): "extratos/extrato janeiro 141Air.csv",
    ("141air", "fev"): "extratos/extrato fevereiro 141Air.csv",
    ("141air", "mar"): "extratos/extrato marco 141Air.csv",
    ("141air", "abr"): "extratos/extrato abril 141Air.csv",
    ("141air", "mai"): "extratos/extrato maio 141Air.csv",
    ("net-air", "jan"): "extratos/extrato janeiro netair.csv",
    ("net-air", "fev"): "extratos/extrato fevereiro netair.csv",
    ("net-air", "mar"): "extratos/extrato marco netair.csv",
    ("net-air", "abr"): "extratos/extrato abril netair.csv",
    ("net-air", "mai"): "extratos/extrato maio netair.csv",
}
WIN_LO, WIN_HI = "2026-01", "2026-05"


def fmt(v):
    return f"{v:>13,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def brt_month(iso):
    try:
        return datetime.fromisoformat(iso).astimezone(timezone(timedelta(hours=-3))).strftime("%Y-%m")
    except (ValueError, TypeError):
        return iso[:7] if iso else ""


async def main():
    slug = sys.argv[1] if len(sys.argv) > 1 else "141air"

    # merge dedupado
    merged = {}
    for m in MONTHS:
        p = os.path.join(BASE, MDIR[m], f"{slug}_payments.json")
        if not os.path.exists(p):
            continue
        for pp in json.load(open(p)).get("payments", []):
            if isinstance(pp, dict):
                pid = str(pp.get("id")); old = merged.get(pid)
                if old is None or (pp.get("date_last_updated") or "") > (old.get("date_last_updated") or ""):
                    merged[pid] = pp

    cap = await run_seller_month(slug, list(merged.values()), state={})

    # ca events por ref
    ca_net_ref = defaultdict(float)
    ca_venc_months = defaultdict(set)     # ref -> set de meses de vencimento
    ca_tipos = defaultdict(set)
    sale_ids = set()
    for e in cap.events:
        base = e.payment_id.split("_")[0]
        if e.tipo in ("receita", "comissao", "frete"):
            sale_ids.add(base)
        ca_net_ref[base] += SIGN.get(e.tipo, 0.0) * e.valor
        vm = (e.vencimento or "")[:7]
        if vm:
            ca_venc_months[base].add(vm)
        ca_tipos[base].add(e.tipo)

    # extrato: net por ref + meses em que o ref aparece + linhas
    ext_net_ref = defaultdict(float)
    ext_months = defaultdict(set)
    ext_lines_ref = defaultdict(list)
    for m in MONTHS:
        ep = EXTRATO_MAP.get((slug, m))
        if not ep or not os.path.exists(os.path.join(BASE, ep)):
            continue
        _, rows = judge.load_extrato(os.path.join(BASE, ep))
        mk = {"jan": "2026-01", "fev": "2026-02", "mar": "2026-03", "abr": "2026-04", "mai": "2026-05"}[m]
        for r in rows:
            ref = str(r["ref"])
            ext_net_ref[ref] += r["net"]
            ext_months[ref].add(mk)
            ext_lines_ref[ref].append((mk, r["type"], r["net"]))

    # universo: refs que o processor tocou (sale) E que aparecem no extrato
    refs = [r for r in sale_ids if r in ext_net_ref]

    buckets = defaultdict(lambda: [0, 0.0, 0.0])  # nome -> [count, Σdiff, Σ|diff|]
    samples = defaultdict(list)
    for ref in refs:
        en = ext_net_ref[ref]
        cn = ca_net_ref[ref]
        d = en - cn
        if abs(d) <= 0.5:
            buckets["OK (≤R$0,50)"][0] += 1
            buckets["OK (≤R$0,50)"][1] += d
            continue
        p = merged.get(ref, {})
        st = p.get("status")
        sd = p.get("status_detail")
        venda_m = brt_month(p.get("date_approved") or p.get("date_created", ""))
        rel_m = brt_month(p.get("money_release_date", ""))
        # boundary: alguma perna (venda aprovada ou liberação) cai FORA da janela jan-mai
        venc_out = any(vm < WIN_LO or vm > WIN_HI for vm in ca_venc_months.get(ref, set()))
        ext_out = False  # extrato só tem jan-mai, então não há perna fora visível no extrato
        is_refund = st in ("refunded", "charged_back")

        if abs(en) < 0.5 and abs(cn) > 0.5:
            name = "T1 ESTRUTURAL: CA lancou, extrato SEM credito na janela (release pos-mai/sob outro ref)"
        elif abs(cn) < 0.5 and abs(en) > 0.5:
            name = "T2 ESTRUTURAL: extrato so 1 perna, CA zerou (estorno cross-month / dinheiro retido)"
        elif venc_out:
            name = "B  ESTRUTURAL: boundary (perna do CA libera fora de jan-mai = dez/jun)"
        elif is_refund:
            name = "V1 ELIMINAVEL: refund, valor diverge (frete/debito-envio nao modelado)"
        elif st in ("approved", "in_mediation"):
            name = "V2 ELIMINAVEL: approved, valor diverge (taxa oculta residual / frete base)"
        else:
            name = f"V3 ELIMINAVEL: outro status={st}"
        b = buckets[name]
        b[0] += 1; b[1] += d; b[2] += abs(d)
        if len(samples[name]) < 6:
            samples[name].append((abs(d), ref, en, cn, st, sd, venda_m, rel_m,
                                  sorted(ca_venc_months.get(ref, set())), sorted(ca_tipos.get(ref, set()))))

    print("=" * 92)
    print(f"DIAGNÓSTICO DE CAUSA-RAIZ DO RESÍDUO — {slug} (jan-mai/2026)")
    print(f"refs venda tocados pelo processor presentes no extrato: {len(refs)}")
    print("=" * 92)
    total_d = sum(b[1] for b in buckets.values())
    print(f"\n{'bucket de causa':<72}{'qtd':>5}{'Σdiff':>13}")
    for name in sorted(buckets, key=lambda k: -abs(buckets[k][1])):
        b = buckets[name]
        print(f"{name:<72}{b[0]:>5}{fmt(b[1])}")
    print(f"\n  Σ resíduo total (ext - ca) = {fmt(total_d)}")

    for name in sorted(samples, key=lambda k: -abs(buckets[k][1])):
        print(f"\n--- amostra: {name}")
        print(f"    {'|diff|':>11} {'ref':<14} {'ext':>11} {'ca':>11} status/detail  venda→rel  venc_CA  tipos")
        for d, ref, en, cn, st, sd, vm, rm, vcm, tp in samples[name]:
            print(f"    {fmt(d)} {ref:<14} {fmt(en)} {fmt(cn)} [{st}/{sd}] {vm}→{rm} {vcm} {tp}")


if __name__ == "__main__":
    asyncio.run(main())
