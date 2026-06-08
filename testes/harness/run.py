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
    ("141air", "mar"): "extratos/extrato marco 141Air.csv",
    ("141air", "abr"): "extratos/extrato abril 141Air.csv",
    ("141air", "mai"): "extratos/extrato maio 141Air.csv",
    ("net-air", "jan"): "extratos/extrato janeiro netair.csv",
    ("net-air", "fev"): "extratos/extrato fevereiro netair.csv",
    ("net-air", "mar"): "extratos/extrato marco netair.csv",
    ("net-air", "abr"): "extratos/extrato abril netair.csv",
    ("net-air", "mai"): "extratos/extrato maio netair.csv",
}
MONTH_DIR = {"jan": "cache_jan2026", "fev": "cache_fev2026", "mar": "cache_mar2026",
             "abr": "cache_abr2026", "mai": "cache_mai2026"}


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

        # [E] FULL CAIXA: decompoe TODAS as linhas do extrato e fecha o caixa do mes.
        # Identidade: FINAL-INITIAL = vendas + non-venda classificado + skip + OTHER.
        # "Bate" = resíduo de vendas ~0 E OTHER ~0.
        sale_refs = set(net_jan.keys())
        venda_ext = nonsale_class = skip_tot = other_tot = 0.0
        other_lines = []
        for r in rows:
            etype, direction, code, pat = judge.classify(r["type"])
            if etype == "__SALE__":
                venda_ext += r["net"]
            elif etype == "__OTHER__":
                other_tot += r["net"]
                other_lines.append((abs(r["net"]), r["type"][:42], r["net"]))
            elif etype is None:
                skip_tot += r["net"]
            else:
                nonsale_class += r["net"]
        total_mov = header["final"] - header["initial"]
        ca_vendas = sum(net_jan.values())
        resid_vendas = venda_ext - ca_vendas
        # skip = coberto pelo classifier da API (non-order). No harness, mp_expenses capturados.
        resid_caixa = resid_vendas + other_tot
        other_lines.sort(reverse=True)
        print(f"\n[E] FULL CAIXA (fecha o mês inteiro?)")
        print(f"    movimento total extrato (FINAL-INITIAL) = {fmt(total_mov)}")
        print(f"    vendas:        extrato {fmt(venda_ext)} | CA(mes) {fmt(ca_vendas)} | resíduo {fmt(resid_vendas)}")
        print(f"    non-venda classificado (ingester rules)  = {fmt(nonsale_class)}")
        print(f"    skip (coberto API non-order)             = {fmt(skip_tot)}")
        print(f"    OTHER (NÃO coberto - precisa regra)      = {fmt(other_tot)}  ({len(other_lines)} linhas)")
        print(f"    >>> RESÍDUO CAIXA (vendas + OTHER)       = {fmt(resid_caixa)}  {'✓ BATE' if abs(resid_caixa) < 50 else '✗ NÃO BATE'}")
        if other_lines:
            print(f"    OTHER top (precisa regra de classificação):")
            for v, t, net in other_lines[:8]:
                print(f"      {fmt(net)}  {t}")


ALL_MONTHS = ["jan", "fev", "mar", "abr", "mai"]


def ext_month_key(date_ddmmyyyy):
    """'01-04-2026' -> '2026-04'."""
    p = date_ddmmyyyy.split("-")
    return f"{p[2]}-{p[1]}" if len(p) == 3 else ""


async def run_timeline(slug, months):
    """Processa cada payment UMA vez (união dedupada de todos os meses), depois bucketa
    os eventos CA por mês de caixa (vencimento) e compara contra o extrato de cada mês.
    Modela produção (payment processado 1x; receita no mês da liberação, estorno no mês
    do estorno) -> evita o double-processing cross-month dos snapshots."""
    # 1. merge dedupado (prefere o snapshot com date_last_updated mais recente)
    merged = {}
    for mes in months:
        ps = load_payments(slug, mes)
        if not ps:
            continue
        for p in ps:
            if not isinstance(p, dict):
                continue
            pid = str(p.get("id"))
            old = merged.get(pid)
            if old is None or (p.get("date_last_updated") or "") > (old.get("date_last_updated") or ""):
                merged[pid] = p
    payments = list(merged.values())
    print(f"\n{'='*88}\n# {slug}  TIMELINE  ({len(payments)} payments únicos, meses={','.join(months)})\n{'='*88}")

    # 2. processa UMA vez (real processor), estado fresco
    cap = await run_seller_month(slug, payments, state={})
    if cap.errors:
        print(f"  ERROS: {len(cap.errors)} (amostra: {cap.errors[:3]})")

    # 3. CA cash por (mes_vencimento, ref base) e set de refs de venda
    ca_by_month = {}        # 'YYYY-MM' -> Σ sign*valor
    ca_by_month_ref = {}    # ('YYYY-MM', ref) -> Σ
    sale_ids = set()
    for e in cap.events:
        base = e.payment_id.split("_")[0]
        if e.tipo in ("receita", "comissao", "frete"):
            sale_ids.add(base)
        m = (e.vencimento or "")[:7]
        v = SIGN.get(e.tipo, 0.0) * e.valor
        ca_by_month[m] = ca_by_month.get(m, 0.0) + v
        ca_by_month_ref[(m, base)] = ca_by_month_ref.get((m, base), 0.0) + v

    # 3b. RESÍDUO DE VALOR (date-independent): Σ extrato por ref (todos meses) vs processor net.
    # Isola erro de VALOR (taxa oculta + refund parcial) do desalinho de DATA.
    ext_total_ref = {}
    for mes in months:
        ext_path = EXTRATO_MAP.get((slug, mes))
        if not ext_path or not os.path.exists(os.path.join(BASE, ext_path)):
            continue
        _, rows = judge.load_extrato(os.path.join(BASE, ext_path))
        for r in rows:
            ref = str(r["ref"])
            if ref in sale_ids:
                ext_total_ref[ref] = ext_total_ref.get(ref, 0.0) + r["net"]
    ca_net_ref = {}
    for (m, ref), v in ca_by_month_ref.items():
        ca_net_ref[ref] = ca_net_ref.get(ref, 0.0) + v
    # só refs que TÊM presença no extrato da janela (venda cujo caixa caiu em jan-mai)
    val_resid = 0.0
    val_absdiff = 0.0
    n_off = 0
    worst_val = []
    for ref in ext_total_ref:
        d = ext_total_ref[ref] - ca_net_ref.get(ref, 0.0)
        val_resid += d
        val_absdiff += abs(d)
        if abs(d) > 0.5:
            n_off += 1
            worst_val.append((abs(d), ref, ext_total_ref[ref], ca_net_ref.get(ref, 0.0)))
    worst_val.sort(reverse=True)
    print(f"\n  RESÍDUO DE VALOR (date-independent, Σ extrato vs processor por ref c/ caixa na janela):")
    print(f"    {len(ext_total_ref)} refs | Σ resíduo = {fmt(val_resid)} | Σ|resíduo| = {fmt(val_absdiff)} | refs off>R$0,50: {n_off}")
    for d, ref, e, c in worst_val[:6]:
        print(f"      {fmt(d)}  ref={ref:<14} ext={fmt(e)} ca={fmt(c)}")

    # 4. por mês: caixa de vendas (extrato sale-lines vs CA) + cobertura OTHER
    print(f"\n  {'mes':<8}{'ext_vendas':>13}{'CA_vendas':>13}{'resíduo':>12}{'OTHER':>11}{'status':>10}")
    tot_resid = 0.0
    for mes in months:
        ext_path = EXTRATO_MAP.get((slug, mes))
        if not ext_path or not os.path.exists(os.path.join(BASE, ext_path)):
            continue
        header, rows = judge.load_extrato(os.path.join(BASE, ext_path))
        mkey = {"jan": "2026-01", "fev": "2026-02", "mar": "2026-03", "abr": "2026-04", "mai": "2026-05"}[mes]
        ext_sales = 0.0
        other = 0.0
        for r in rows:
            etype, direction, code, pat = judge.classify(r["type"])
            ref = str(r["ref"])
            if ref in sale_ids:
                ext_sales += r["net"]
            elif etype == "__OTHER__":
                other += r["net"]
        ca_sales = sum(v for (m, ref), v in ca_by_month_ref.items() if m == mkey and ref in sale_ids)
        resid = ext_sales - ca_sales
        tot_resid += resid
        ok = abs(resid) + abs(other) < 100
        print(f"  {mes:<8}{fmt(ext_sales)}{fmt(ca_sales)}{fmt(resid)}{fmt(other)}{'  ✓ BATE' if ok else '  ✗':>10}")
    print(f"  {'TOTAL':<8}{'':>13}{'':>13}{fmt(tot_resid)}")


async def main():
    if len(sys.argv) < 3:
        print("uso: python3 -m testes.harness.run <slug> <mes[,mes]|timeline>")
        return
    slug = sys.argv[1]
    if sys.argv[2] == "timeline":
        await run_timeline(slug, [m for m in ALL_MONTHS if load_payments(slug, m)])
        return
    meses = sys.argv[2].split(",")
    state = {}  # idempotencia compartilhada entre meses (em ordem)
    for mes in meses:
        payments = load_payments(slug, mes)
        if payments is None:
            print(f"\n!! sem cache de payments pra {slug} {mes} (testes/{MONTH_DIR.get(mes)}/{slug}_payments.json)")
            continue
        cap = await run_seller_month(slug, payments, state=state)
        reconcile(slug, mes, cap, payments=payments)


if __name__ == "__main__":
    asyncio.run(main())
