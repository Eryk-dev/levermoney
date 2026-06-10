"""PROVA DO NÍVEL DE LANÇAMENTO — grupo + complemento == extrato, por ref.

Política (decisão do dono): disputa = cancelamento ("nunca virou venda");
nunca-lançado não ganha par fantasma; toda divergência vira lançamento
CATEGORIZADO via app.services.complemento (função real de produção).

Elegibilidade offline:
  - disputa/cancelled: status terminal -> elegível.
  - approved/partial: elegível só se tem liberação in-window (ciclo chegou no caixa);
    sem liberação = boundary (ciclo aberto) -> não complementa (em produção o gate é
    money_release_status + idade da última linha).

Uso: python3 -m testes.harness.lancamento_100 [141air|net-air]
"""
import asyncio
import json
import os
import sys
import importlib.util
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from testes.harness.dryrun import run_seller_month, SIGN
from app.services.complemento import plan_complemento  # FUNÇÃO REAL DE PRODUÇÃO

_jp = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "judge_caixa_jan2026.py")
_s = importlib.util.spec_from_file_location("judge", _jp)
judge = importlib.util.module_from_spec(_s); _s.loader.exec_module(judge)

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MONTHS = ["jan", "fev", "mar", "abr", "mai"]
MDIR = {m: f"cache_{m}2026" for m in MONTHS}
EMAP = {"jan": "janeiro", "fev": "fevereiro", "mar": "marco", "abr": "abril", "mai": "maio"}
EXTSL = {"141air": "141Air", "net-air": "netair"}
WIN_LO, WIN_HI = "2026-01-01", "2026-05-31"


def fmt(v):
    return f"{v:>14,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def iso(d):
    p = d.split("-")
    return f"{p[2]}-{p[1]}-{p[0]}" if len(p) == 3 else d


async def prova(slug):
    extsl = EXTSL[slug]
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

    # grupo lançado por ref, por TIPO (assinado) — sufixos viram tipos próprios
    net_por_tipo_ref: dict[str, dict] = defaultdict(lambda: defaultdict(float))
    for e in cap.events:
        base = e.payment_id.split("_")[0]
        if e.payment_id.endswith("_subsidy"):
            tipo = "subsidio"
        elif e.payment_id.endswith("_hiddenfee"):
            tipo = "hiddenfee"
        else:
            tipo = e.tipo
        sign = SIGN.get(e.tipo, 0.0)
        if tipo == "subsidio":
            sign = +1.0
        elif tipo == "hiddenfee":
            sign = -1.0
        net_por_tipo_ref[base][tipo] += sign * e.valor

    # extrato por ref (refs de payments de VENDA com order): total + tem-liberação + última data
    ext_total = defaultdict(float)
    tem_liberacao = set()
    ultima_data = {}
    venda_refs = {pid for pid, p in merged.items() if (p.get("order") or {}).get("id")}
    for m in MONTHS:
        path = os.path.join(BASE, "extratos", f"extrato {EMAP[m]} {extsl}.csv")
        if not os.path.exists(path):
            continue
        _, rows = judge.load_extrato(path)
        for r in rows:
            d = iso(r["date"])
            if d < WIN_LO or d > WIN_HI:
                continue
            ref = str(r["ref"])
            if ref not in venda_refs:
                continue
            ext_total[ref] += r["net"]
            ultima_data[ref] = max(ultima_data.get(ref, ""), d)
            et, _, _, _ = judge.classify(r["type"])
            if et == "__SALE__":
                tem_liberacao.add(ref)

    # universo: refs de venda com presença no extrato OU com grupo lançado
    universo = set(ext_total) | {r for r in net_por_tipo_ref if r in venda_refs}

    fechados = 0
    inelegiveis = 0
    residuo_pos = 0.0
    comp_por_cat = defaultdict(lambda: [0, 0.0])
    pior = []
    for ref in universo:
        p = merged.get(ref, {})
        status = p.get("status")
        grupo = dict(net_por_tipo_ref.get(ref, {}))
        etotal = round(ext_total.get(ref, 0.0), 2)
        gtotal = round(sum(grupo.values()), 2)

        is_disputa = status in ("refunded", "charged_back", "cancelled")
        eleg = is_disputa or (ref in tem_liberacao)
        if not eleg:
            # boundary: ciclo não chegou no caixa dentro da janela
            inelegiveis += 1
            continue

        comps = plan_complemento(
            ref, p, grupo, etotal,
            data_lancamento=ultima_data.get(ref, WIN_HI), elegivel=True,
        )
        soma_comp = round(sum(c.valor for c in comps), 2)
        residuo = round(etotal - gtotal - soma_comp, 2)
        for c in comps:
            comp_por_cat[c.categoria][0] += 1
            comp_por_cat[c.categoria][1] += c.valor
        if abs(residuo) < 0.01:
            fechados += 1
        else:
            residuo_pos += abs(residuo)
            pior.append((abs(residuo), ref, etotal, gtotal, soma_comp, status))

    pior.sort(reverse=True)
    print("=" * 86)
    print(f"LANÇAMENTO 100% — {slug}  (plan_complemento real, política disputa=cancelamento)")
    print("=" * 86)
    print(f"  universo: {len(universo)} refs de venda c/ extrato ou grupo "
          f"| elegíveis: {len(universo)-inelegiveis} | boundary (ciclo aberto): {inelegiveis}")
    print(f"  >> REFS FECHADOS (grupo+complemento == extrato): {fechados}/{len(universo)-inelegiveis}"
          f"   resíduo remanescente = {fmt(residuo_pos)}")
    print(f"\n  complementos planejados (o que será postado, por categoria):")
    for cat, (n, v) in sorted(comp_por_cat.items(), key=lambda kv: kv[1][1]):
        print(f"    {cat:<18} {n:>6} lançamentos  Σ {fmt(v)}")
    if pior:
        print(f"\n  piores resíduos (não deveriam existir):")
        for d, ref, e, g, c, st in pior[:5]:
            print(f"    {fmt(d)} ref={ref} ext={fmt(e)} grupo={fmt(g)} comp={fmt(c)} [{st}]")
    return fechados, len(universo) - inelegiveis


async def main():
    slugs = [sys.argv[1]] if len(sys.argv) > 1 else ["141air", "net-air"]
    for slug in slugs:
        await prova(slug)
        print()


if __name__ == "__main__":
    asyncio.run(main())
