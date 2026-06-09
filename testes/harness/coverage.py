"""JUIZ de cobertura total: processor + extrato_ingester vs extrato (a verdade).

O harness.run roda SÓ o processor. Mas em produção o caixa do CA = processor (vendas)
+ extrato_ingester (linhas non-venda lançadas ao VALOR do extrato). Este script simula
AMBOS e mede o resíduo REAL de produção.

Modelo:
- processor: eventos assinados (receita - comissao - frete - estorno + estorno_taxa).
- ingester: replica a decisão de cobertura REAL (extrato_ingester.py:723-761). Linha
  ingerida entra ao VALOR do extrato (face) -> cancela exata -> nunca gera diff.
- resíduo = Σ extrato - Σ CA. Só sobra de: (a) GAP (linha que ninguém cobre),
  (b) mismatch processor (líquido da venda != liberação do extrato), (c) boundary/timing.

Flag --fix2: simula ingerir debito_envio_ml mesmo em ref de venda conhecida (fecha o gap).

Uso: python3 -m testes.harness.coverage 141air [--fix2]
"""
import asyncio
import json
import os
import sys
import importlib.util
from collections import defaultdict

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
WIN_LO, WIN_HI = "2026-01", "2026-05"

# Réplica da lista "sempre ingerir mesmo se ref tem payment" (extrato_ingester.py:753-756)
ALWAYS_INGEST = {"reembolso_disputa", "reembolso_generico", "entrada_dinheiro",
                 "dinheiro_retido", "liberacao_cancelada"}


def fmt(v):
    return f"{v:>14,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


async def main():
    slug = sys.argv[1] if len(sys.argv) > 1 else "141air"
    fix2 = "--fix2" in sys.argv
    always = set(ALWAYS_INGEST) | ({"debito_envio_ml"} if fix2 else set())

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

    # processor: net assinado por ref + refs com receita (payment_refs) e com estorno (refunded_refs)
    proc_net_all = 0.0
    proc_net_win = 0.0      # só eventos com vencimento em jan-mai
    payment_refs = set()
    refunded_refs = set()
    for e in cap.events:
        base = e.payment_id.split("_")[0]
        v = SIGN.get(e.tipo, 0.0) * e.valor
        proc_net_all += v
        venc = (e.vencimento or "")[:7]
        if WIN_LO <= venc <= WIN_HI:
            proc_net_win += v
        if e.tipo == "receita" and not e.payment_id.endswith("_subsidy"):
            payment_refs.add(base)
        if e.tipo in ("estorno", "partial_refund"):
            refunded_refs.add(base)
    # refs cobertos por mp_expenses do classifier (non-order)
    for r in cap.mp_expenses:
        pid = str((r or {}).get("payment_id") or "")
        if pid:
            payment_refs.add(pid.split(":")[0])

    # extrato + decisão do ingester por linha
    ext_total = 0.0
    ingested = 0.0
    skip_liberacao = 0.0
    gap = defaultdict(float)        # etype -> Σ net de linhas NÃO cobertas (nem processor nem ingester)
    dedup_divida = 0.0              # debito_divida coberto pelo estorno do processor
    skip_other = 0.0
    for m in MONTHS:
        ep = EXTRATO_MAP.get((slug, m))
        if not ep or not os.path.exists(os.path.join(BASE, ep)):
            continue
        _, rows = judge.load_extrato(os.path.join(BASE, ep))
        for r in rows:
            net = r["net"]
            ext_total += net
            ref = str(r["ref"])
            etype, direction, code, pat = judge.classify(r["type"])
            if etype == "__SALE__":
                skip_liberacao += net           # coberto pela receita do processor
                continue
            if etype is None:
                skip_other += net               # pix/boleto/compra ML -> coberto/interno
                continue
            if etype == "__OTHER__":
                gap["__OTHER__"] += net          # sem regra -> cauda manual
                continue
            # tem regra de ingester
            if ref in payment_refs:
                if etype == "debito_divida_disputa":
                    if ref in refunded_refs:
                        dedup_divida += net      # coberto pelo estorno do processor
                        continue
                    ingested += net              # payment não-estornado -> ingere
                    continue
                if etype in always:
                    ingested += net
                    continue
                # else: ingester pula como "already_covered" MAS processor não modela = GAP
                gap[etype] += net
                continue
            ingested += net                      # ref sem payment -> ingere normal

    ca_total = proc_net_all + ingested
    residual = ext_total - ca_total
    boundary = proc_net_all - proc_net_win       # eventos processor fora da janela

    print("=" * 78)
    print(f"JUIZ DE COBERTURA TOTAL — {slug} (jan-mai/2026){'  [+fix2 envio]' if fix2 else ''}")
    print("=" * 78)
    print(f"  Σ EXTRATO (movimento real)            = {fmt(ext_total)}")
    print(f"  Σ CA = processor + ingester(face)     = {fmt(ca_total)}")
    print(f"    processor (todos eventos)           = {fmt(proc_net_all)}")
    print(f"      dos quais vencem fora jan-mai      = {fmt(boundary)}  (boundary)")
    print(f"    ingester (linhas ao valor extrato)  = {fmt(ingested)}")
    print(f"  {'-'*60}")
    print(f"  >>> RESÍDUO (ext - CA)                = {fmt(residual)}")
    print(f"\n  Decomposição do que o CA NÃO cobre ao valor do extrato:")
    print(f"    liberação (deve casar c/ venda proc) = {fmt(skip_liberacao)}")
    print(f"    debito_divida deduped (estorno proc) = {fmt(dedup_divida)}")
    print(f"    skip interno (pix/boleto/compra)     = {fmt(skip_other)}")
    tot_gap = sum(gap.values())
    print(f"    GAP (ninguém cobre)                  = {fmt(tot_gap)}")
    for et in sorted(gap, key=lambda k: gap[k]):
        print(f"        {et:<28} {fmt(gap[et])}")
    print(f"\n  IDENTIDADE: resíduo = (liberação+dedup+skip_other+gap) - processor_net")
    print(f"    {fmt(skip_liberacao+dedup_divida+skip_other+tot_gap)} - {fmt(proc_net_all)} = "
          f"{fmt(skip_liberacao+dedup_divida+skip_other+tot_gap - proc_net_all)}")


if __name__ == "__main__":
    asyncio.run(main())
