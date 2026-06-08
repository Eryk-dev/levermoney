"""CLI do harness real-code dry-run.

Roda o processor/classifier REAL (via dryrun.run_seller_month) e reconcilia o
ledger capturado contra o extrato real.

Uso:
    python3 -m testes.harness.run 141air jan
    python3 -m testes.harness.run 141air jan,fev
"""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from testes.harness.dryrun import run_seller_month, SIGN
# reusa parsing do juiz da Fase 0
import importlib.util
_judge_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "judge_caixa_jan2026.py")
_spec = importlib.util.spec_from_file_location("judge", _judge_path)
judge = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(judge)

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# extrato CSV por (slug, mes)
EXTRATO_MAP = {
    ("141air", "jan"): "extratos/extrato janeiro 141Air.csv",
    ("141air", "fev"): "extratos/extrato fevereiro 141Air.csv",
    ("net-air", "jan"): "extratos/extrato janeiro netair.csv",
    ("net-air", "fev"): "extratos/extrato fevereiro netair.csv",
    ("netparts-sp", "jan"): "extratos/extrato janeiro netparts.csv",
    ("netparts-sp", "fev"): "extratos/extrato fevereiro netparts.csv",
    ("easy-utilidades", "jan"): "extratos/extrato janeiro Easyutilidades.csv",
    ("easy-utilidades", "fev"): "extratos/extrato fevereiro easypeasy.csv",
}
MONTH_DIR = {"jan": "cache_jan2026", "fev": "cache_fev2026"}


def fmt(v):
    return f"{v:>13,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def load_payments(slug, mes):
    path = os.path.join(BASE, MONTH_DIR.get(mes, ""), f"{slug}_payments.json")
    if not os.path.exists(path):
        return None
    raw = json.load(open(path))
    return raw.get("payments", raw) if isinstance(raw, dict) else raw


def extrato_net_by_ref(rows):
    """ref_id -> soma de TODAS as linhas do extrato para esse ref (lifecycle completo no mes)."""
    out = {}
    for r in rows:
        out[str(r["ref"])] = out.get(str(r["ref"]), 0.0) + r["net"]
    return out


def reconcile(slug, mes, cap, payments=None):
    ext_path = EXTRATO_MAP.get((slug, mes))
    print(f"\n{'='*88}\n# {slug}  {mes}/2026   (eventos CA capturados={len(cap.events)}, mp_expenses={len(cap.mp_expenses)})\n{'='*88}")
    if cap.errors:
        print(f"  ERROS no processamento: {len(cap.errors)} (amostra)")
        for pid, et, msg in cap.errors[:5]:
            print(f"    payment {pid}: {et}: {msg}")

    # net de caixa capturado por payment (normaliza id base: tira _subsidy/_hiddenfee)
    net_by_pid = {}
    for e in cap.events:
        base = e.payment_id.split("_")[0]
        net_by_pid[base] = net_by_pid.get(base, 0.0) + SIGN.get(e.tipo, 0.0) * e.valor

    if not ext_path or not os.path.exists(os.path.join(BASE, ext_path)):
        print(f"  [sem extrato pra {slug} {mes}] — recon de vendas pulado")
        print(f"  Σ net capturado (todos payments) = {fmt(sum(net_by_pid.values()))}")
        return
    header, rows = judge.load_extrato(os.path.join(BASE, ext_path))

    # [A] ancora
    sum_net, exp_final, anchor_diff, drift_lines, max_drift = judge.run_anchor(header, rows)
    print(f"\n[A] ANCORA  INITIAL {fmt(header['initial'])} + Σnet {fmt(sum_net)} = {fmt(exp_final)} vs FINAL {fmt(header['final'])}  diff={fmt(anchor_diff)} {'OK' if abs(anchor_diff)<0.01 else 'X'}")

    # status por payment id (pra separar approved limpo vs refunded timing)
    pstat = {}
    if payments:
        for p in payments:
            if isinstance(p, dict):
                pstat[str(p.get("id"))] = (p.get("status"), p.get("status_detail"))

    # [C] recon de vendas: net CA capturado (processor REAL) vs net do extrato por ref
    # (soma TODAS as linhas do ref no mes = lifecycle: liberacao + refund/debito)
    ext_ref = extrato_net_by_ref(rows)
    # so refs que o processor tocou (tem evento CA) — vendas
    buckets = {"approved": [0, 0.0], "refunded": [0, 0.0], "outro": [0, 0.0]}
    sum_ext = sum_cap = sum_absdiff = 0.0
    worst = []
    for ref, cap_net in net_by_pid.items():
        if ref not in ext_ref:
            continue
        en = ext_ref[ref]
        sum_ext += en
        sum_cap += cap_net
        d = en - cap_net
        sum_absdiff += abs(d)
        st = (pstat.get(ref) or ("?", "?"))[0]
        key = "approved" if st in ("approved", "in_mediation") else ("refunded" if st in ("refunded", "charged_back") else "outro")
        buckets[key][0] += 1
        buckets[key][1] += d
        if abs(d) > 0.01:
            worst.append((abs(d), ref, en, cap_net, st))
    worst.sort(reverse=True)
    print(f"\n[C] RECON VENDAS (processor REAL vs extrato, net por ref = lifecycle completo)")
    print(f"    refs com evento CA casados c/ extrato: {sum(b[0] for b in buckets.values())}")
    print(f"    Σ extrato (refs vendas) = {fmt(sum_ext)}")
    print(f"    Σ net CA (capturado)    = {fmt(sum_cap)}")
    print(f"    >> NET_DIFF total       = {fmt(sum_ext - sum_cap)}   Σ|diff|={fmt(sum_absdiff)}")
    print(f"    por status:")
    for k, (n, dsum) in buckets.items():
        print(f"      {k:<10} refs={n:>4}  Σdiff={fmt(dsum)}")
    if worst:
        print(f"    piores (|diff|, ref, extrato_lifecycle, CA, status):")
        for d, ref, en, cn, st in worst[:8]:
            print(f"      {fmt(d)}  ref={ref:<14} ext={fmt(en)} ca={fmt(cn)} [{st}]")

    # [D] recon DATE-AWARE: caixa do CA so com eventos cujo vencimento cai no MES do extrato.
    # Estornos de vendas refunded em mes posterior caem fora -> nao poluem o caixa do mes.
    month_key = {"jan": "2026-01", "fev": "2026-02", "mar": "2026-03",
                 "abr": "2026-04", "mai": "2026-05"}.get(mes)
    if month_key:
        net_jan = {}
        spill = 0.0
        for e in cap.events:
            base = e.payment_id.split("_")[0]
            venc = (e.vencimento or "")[:7]
            val = SIGN.get(e.tipo, 0.0) * e.valor
            if venc == month_key:
                net_jan[base] = net_jan.get(base, 0.0) + val
            else:
                spill += val
        sum_ext_d = sum_cap_d = sum_absdiff_d = 0.0
        bkt = {"approved": [0, 0.0], "refunded": [0, 0.0]}
        for ref, en in ext_ref.items():
            cn = net_jan.get(ref)
            if cn is None:
                continue
            sum_ext_d += en
            sum_cap_d += cn
            d = en - cn
            sum_absdiff_d += abs(d)
            st = (pstat.get(ref) or ("?",))[0]
            k = "approved" if st in ("approved", "in_mediation") else "refunded"
            bkt[k][0] += 1
            bkt[k][1] += d
        print(f"\n[D] CAIXA DATE-AWARE (so eventos com vencimento em {month_key})")
        print(f"    Σ extrato = {fmt(sum_ext_d)} | Σ CA(mes) = {fmt(sum_cap_d)} | NET_DIFF = {fmt(sum_ext_d-sum_cap_d)}  Σ|diff|={fmt(sum_absdiff_d)}")
        print(f"    spill (estornos/eventos fora do mes, vao p/ outro mes) = {fmt(spill)}")
        for k, (n, dsum) in bkt.items():
            print(f"      {k:<10} refs={n:>4}  Σdiff={fmt(dsum)}")


async def main():
    if len(sys.argv) < 3:
        print("uso: python3 -m testes.harness.run <slug> <mes[,mes]>")
        return
    slug = sys.argv[1]
    meses = sys.argv[2].split(",")
    for mes in meses:
        payments = load_payments(slug, mes)
        if payments is None:
            print(f"\n!! sem cache de payments pra {slug} {mes} (testes/{MONTH_DIR.get(mes)}/{slug}_payments.json)")
            continue
        cap = await run_seller_month(slug, payments)
        reconcile(slug, mes, cap, payments=payments)


if __name__ == "__main__":
    asyncio.run(main())
