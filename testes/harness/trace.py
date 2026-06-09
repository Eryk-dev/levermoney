"""Tracer por ref: dump cru do payment + TODAS as linhas do extrato + eventos CA.

Responde: o resíduo do bucket A é erro de VALOR real (taxa retida no refund) ou
artefato de TIMING/mapping (perna de liberação em outro ref/mês)?

Uso: python3 -m testes.harness.trace 141air 147642568926 142959458860
"""
import asyncio
import json
import os
import sys
import importlib.util

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from testes.harness.dryrun import run_seller_month, SIGN

_jp = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "judge_caixa_jan2026.py")
_s = importlib.util.spec_from_file_location("judge", _jp)
judge = importlib.util.module_from_spec(_s); _s.loader.exec_module(judge)

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MONTHS = ["jan", "fev", "mar", "abr", "mai"]
MDIR = {"jan": "cache_jan2026", "fev": "cache_fev2026", "mar": "cache_mar2026",
        "abr": "cache_abr2026", "mai": "cache_mai2026"}
EXTRATO_MAP = {
    ("141air", "jan"): "extratos/extrato janeiro 141Air.csv", ("141air", "fev"): "extratos/extrato fevereiro 141Air.csv",
    ("141air", "mar"): "extratos/extrato marco 141Air.csv", ("141air", "abr"): "extratos/extrato abril 141Air.csv",
    ("141air", "mai"): "extratos/extrato maio 141Air.csv",
    ("net-air", "jan"): "extratos/extrato janeiro netair.csv", ("net-air", "fev"): "extratos/extrato fevereiro netair.csv",
    ("net-air", "mar"): "extratos/extrato marco netair.csv", ("net-air", "abr"): "extratos/extrato abril netair.csv",
    ("net-air", "mai"): "extratos/extrato maio netair.csv",
}


def fmt(v):
    return f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


async def main():
    slug = sys.argv[1]
    targets = set(sys.argv[2:])

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
    ev_by_ref = {}
    for e in cap.events:
        ev_by_ref.setdefault(e.payment_id.split("_")[0], []).append(e)

    # extrato: todas as linhas, por ref
    ext_by_ref = {}
    for m in MONTHS:
        ep = EXTRATO_MAP.get((slug, m))
        if not ep or not os.path.exists(os.path.join(BASE, ep)):
            continue
        _, rows = judge.load_extrato(os.path.join(BASE, ep))
        for r in rows:
            ext_by_ref.setdefault(str(r["ref"]), []).append((m, r["date"], r["type"], r["net"]))

    for ref in targets:
        print("=" * 92)
        print(f"REF {ref}  ({slug})")
        print("=" * 92)
        p = merged.get(ref)
        if p:
            print(f"  PAYMENT: status={p.get('status')}/{p.get('status_detail')} "
                  f"amount={fmt(float(p.get('transaction_amount') or 0))} "
                  f"refunded={fmt(float(p.get('transaction_amount_refunded') or 0))}")
            print(f"           date_approved={p.get('date_approved')} money_release={p.get('money_release_date')}")
            print(f"           shipping_amount={p.get('shipping_amount')}  operation_type={p.get('operation_type')}")
            # charges
            chs = p.get("charges_details") or []
            print(f"           charges_details ({len(chs)}):")
            for ch in chs:
                accts = ch.get("accounts") or {}
                amts = ch.get("amounts") or {}
                print(f"             type={ch.get('type'):<10} name={str(ch.get('name')):<22} "
                      f"from={str(accts.get('from')):<10} to={str(accts.get('to')):<10} "
                      f"orig={amts.get('original')} refunded={amts.get('refunded')}")
        else:
            print("  PAYMENT: (NÃO está no cache)")
        print(f"\n  EVENTOS CA capturados:")
        ca_net = 0.0
        for e in sorted(ev_by_ref.get(ref, []), key=lambda x: (x.vencimento or "")):
            v = SIGN.get(e.tipo, 0.0) * e.valor
            ca_net += v
            print(f"    {e.tipo:<14} valor={fmt(e.valor):>12} sign_net={fmt(v):>12} venc={e.vencimento} comp={e.competencia} cat={e.categoria}")
        print(f"    -> CA net = {fmt(ca_net)}")
        print(f"\n  LINHAS DO EXTRATO (ref={ref}):")
        ext_net = 0.0
        for m, d, t, net in sorted(ext_by_ref.get(ref, []), key=lambda x: x[1]):
            ext_net += net
            print(f"    [{m}] {d}  {t:<46} net={fmt(net):>12}")
        print(f"    -> extrato net = {fmt(ext_net)}")
        print(f"\n  >>> DIFF (ext - ca) = {fmt(ext_net - ca_net)}\n")


if __name__ == "__main__":
    asyncio.run(main())
