"""GABARITO — a régua oficial de sucesso do conciliador.

UM número por seller: Σ|diff| in-window (jan-mai) entre o que o CÓDIGO lança (processor +
ingester, com a dedup real) e o EXTRATO, por ref. Quanto menor, melhor (0 = perfeito).

Roda o processor REAL (via harness) + replica a decisão de ingestão do extrato_ingester
(incl. as dedups de fix1/2/3b). Non-sale ingerido ao valor do extrato.

Uso: python3 -m testes.harness.gabarito 141air
     python3 -m testes.harness.gabarito           # ambos (141air + net-air)
"""
import asyncio
import json
import os
import sys
import importlib.util

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from testes.harness.dryrun import run_seller_month, SIGN
from app.services.extrato_ingester import _is_sale_fee_refund

_jp = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "judge_caixa_jan2026.py")
_s = importlib.util.spec_from_file_location("judge", _jp)
judge = importlib.util.module_from_spec(_s); _s.loader.exec_module(judge)

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MONTHS = ["jan", "fev", "mar", "abr", "mai"]
MDIR = {"jan": "cache_jan2026", "fev": "cache_fev2026", "mar": "cache_mar2026",
        "abr": "cache_abr2026", "mai": "cache_mai2026"}
EMAP = {"jan": "janeiro", "fev": "fevereiro", "mar": "marco", "abr": "abril", "mai": "maio"}
EXTSL = {"141air": "141Air", "net-air": "netair"}
WIN_LO, WIN_HI = "2026-01-01", "2026-05-31"
ALWAYS_INGEST = {"reembolso_disputa", "reembolso_generico", "entrada_dinheiro",
                 "dinheiro_retido", "liberacao_cancelada", "debito_envio_ml"}


def fmt(v):
    return f"{v:>13,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def iso(d):
    p = d.split("-")
    return f"{p[2]}-{p[1]}-{p[0]}" if len(p) == 3 else d


async def gabarito(slug):
    extsl = EXTSL[slug]
    # 1. processor real
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
    payment_ids = {str(pid) for pid in merged}
    refunded_ids = {str(pid) for pid, p in merged.items()
                    if p.get("status") in ("refunded", "charged_back")}

    # code lançado por ref. Só refs de VENDA (premissa: non-sale lançado ao valor do extrato -> 0).
    code_ref = {}
    sale_ids = set()
    for e in cap.events:
        b = e.payment_id.split("_")[0]
        if e.tipo in ("receita", "comissao", "frete"):
            sale_ids.add(b)
        code_ref[b] = code_ref.get(b, 0.0) + SIGN.get(e.tipo, 0.0) * e.valor

    # 2. extrato por ref (só refs de venda) + linhas suplementares que o ingester adiciona
    ext_ref = {}
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
            if ref not in sale_ids:
                continue  # non-sale: premissa = lançado ao valor do extrato (diff 0)
            net = r["net"]
            ext_ref[ref] = ext_ref.get(ref, 0.0) + net
            etype, direction, code, pat = judge.classify(r["type"])
            if etype in ("__SALE__", None):
                continue  # liberação (processor cobre) / skip
            # ref de venda com linha suplementar (retido, débito envio, reembolso): replica ingester
            if etype == "debito_divida_disputa" and ref in refunded_ids:
                continue  # dedup disputa
            if _is_sale_fee_refund(etype, r["type"]) and ref in refunded_ids:
                continue  # dedup fee-refund (fix3b)
            if etype in ALWAYS_INGEST or etype == "__OTHER__":
                code_ref[ref] = code_ref.get(ref, 0.0) + net  # ingester complementa ao valor do extrato

    # 3. diff por ref de venda COM presença no extrato in-window (exclui boundary óbvio:
    #    venda liberada fora de jan-mai, que reconcilia no seu próprio mês)
    sum_abs = 0.0
    n_off = 0
    real_abs = 0.0
    real_n = 0
    worst = []
    for ref in ext_ref:
        c = code_ref.get(ref, 0.0)
        e = ext_ref.get(ref, 0.0)
        d = round(c - e, 2)
        if abs(d) < 0.5:
            continue
        sum_abs += abs(d)
        n_off += 1
        worst.append((abs(d), ref, e, c))
        # erro REAL = ambas as pernas presentes (não é boundary cross-window)
        if abs(e) >= 1 and abs(c) >= 1:
            real_abs += abs(d)
            real_n += 1
    worst.sort(reverse=True)
    return sum_abs, n_off, real_abs, real_n, len(ext_ref), worst


async def main():
    slugs = [sys.argv[1]] if len(sys.argv) > 1 else ["141air", "net-air"]
    print("=" * 72)
    print("GABARITO — Σ|diff| full-ledger in-window (jan-mai). Menor = melhor. 0 = perfeito.")
    print("=" * 72)
    for slug in slugs:
        sa, n, real_abs, real_n, total, worst = await gabarito(slug)
        print(f"\n# {slug}   ({total} refs de venda c/ caixa in-window)")
        print(f"  >> ERRO REAL (valores errados, ambas pernas) = {fmt(real_abs)}  ({real_n} refs)  <- O NÚMERO QUE IMPORTA")
        print(f"     Σ|diff| total (inclui timing cross-window)  = {fmt(sa)}  ({n} refs)")
        if worst:
            print(f"     piores (|diff|, ref, extrato, código):")
            for d, ref, e, c in worst[:5]:
                tag = "REAL" if abs(e) >= 1 and abs(c) >= 1 else "boundary"
                print(f"       {fmt(d)}  ref={ref:<14} ext={fmt(e)} cod={fmt(c)} [{tag}]")
    print()


if __name__ == "__main__":
    asyncio.run(main())
