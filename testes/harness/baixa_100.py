"""PROVA EMPÍRICA — baixa extrato-dirigida fecha o caixa?

Roda a FUNÇÃO REAL de produção `app.services.baixas_extrato.plan_baixas_from_extrato`
(a mesma que `baixas_extrato_runner.plan_for_seller` chama em prod) contra:
  - parcelas abertas = recebível por venda = net da venda que o processor REAL calcula
    (receita − comissão − frete), via harness dry-run (zero escrita).
  - extrato_lines = linhas "Liberação de dinheiro" REAIS dos CSVs (jan-mai), valor+data reais.

Pergunta: a baixa dirigida pelo extrato reproduz o caixa do banco ao centavo, e o resíduo
(erro de cálculo do processor) vira AJUSTE explícito em vez de drift escondido?

Uso: python3 -m testes.harness.baixa_100 [141air|net-air]
"""
import asyncio
import json
import os
import sys
import importlib.util

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from testes.harness.dryrun import run_seller_month, SIGN
from app.services.baixas_extrato import plan_baixas_from_extrato  # <-- FUNÇÃO REAL DE PRODUÇÃO

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


def fmt(v):
    return f"{v:>14,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def iso(d):
    p = d.split("-")
    return f"{p[2]}-{p[1]}-{p[0]}" if len(p) == 3 else d


async def analyze(slug):
    extsl = EXTSL[slug]

    # 1. processor REAL -> recebível (net da venda) por ref
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

    recv = {}          # ref -> net da venda (receita - comissao - frete), sale-side só
    sale_refs = set()
    for e in cap.events:
        b = e.payment_id.split("_")[0]
        if e.tipo in ("receita", "comissao", "frete"):
            recv[b] = recv.get(b, 0.0) + SIGN[e.tipo] * e.valor
            if e.tipo == "receita":
                sale_refs.add(b)

    # 2. extrato: linhas de LIBERAÇÃO (crédito de venda) reais, in-window
    extrato_lines = []
    for m in MONTHS:
        path = os.path.join(BASE, "extratos", f"extrato {EMAP[m]} {extsl}.csv")
        if not os.path.exists(path):
            continue
        _, rows = judge.load_extrato(path)
        for r in rows:
            d = iso(r["date"])
            if d < WIN_LO or d > WIN_HI:
                continue
            etype, _, _, _ = judge.classify(r["type"])
            if etype == "__SALE__":
                extrato_lines.append({"ref": str(r["ref"]), "net": r["net"], "date": d})
    extrato_lines.sort(key=lambda x: x["date"])

    # 3. parcelas abertas = recebível (net da venda). Só refs com net > 0.
    parcelas = [{"id": f"R-{ref}", "payment_id": ref, "nao_pago": round(recv[ref], 2)}
                for ref in sale_refs if recv.get(ref, 0.0) > 0.009]
    parcela_refs = {p["payment_id"] for p in parcelas}

    # 4. ===== RODA A FUNÇÃO REAL DE PRODUÇÃO =====
    result = plan_baixas_from_extrato(extrato_lines, parcelas)

    # 5. métricas honestas
    sum_baixa = round(sum(b.valor for b in result.baixas), 2)
    sum_ajuste = round(sum(b.ajuste for b in result.baixas), 2)
    # extrato de liberação que CASA com um recebível (exclui boundary: venda aprovada antes da janela)
    ext_matched = round(sum(ln["net"] for ln in extrato_lines if ln["ref"] in parcela_refs), 2)
    ext_total = round(sum(ln["net"] for ln in extrato_lines), 2)
    ext_sem_parcela = round(sum(x["valor"] for x in result.sem_parcela), 2)
    recv_total = round(sum(p["nao_pago"] for p in parcelas), 2)
    nunca_total = round(sum(x["saldo"] for x in result.nunca_baixou), 2)

    # caixa por mês: a baixa reproduz o crédito do extrato?
    baixa_by_month, ext_by_month = {}, {}
    for b in result.baixas:
        baixa_by_month[b.data_pagamento[:7]] = baixa_by_month.get(b.data_pagamento[:7], 0.0) + b.valor
    for ln in extrato_lines:
        if ln["ref"] in parcela_refs:
            ext_by_month[ln["date"][:7]] = ext_by_month.get(ln["date"][:7], 0.0) + ln["net"]

    print("=" * 84)
    print(f"BAIXA EXTRATO-DIRIGIDA — PROVA DE CAIXA — {slug}  (função real plan_baixas_from_extrato)")
    print("=" * 84)
    print(f"\n  payments processados: {len(merged)}   refs de venda: {len(sale_refs)}")
    print(f"  linhas de liberação no extrato (jan-mai): {len(extrato_lines)}")
    print(f"  baixas planejadas: {len(result.baixas)}   nunca_baixou: {len(result.nunca_baixou)}   "
          f"sem_parcela: {len(result.sem_parcela)}")

    print(f"\n  --- CAIXA (o que entra no CA via baixa vs o que o banco creditou) ---")
    print(f"  Σ extrato liberação (casado c/ recebível) = {fmt(ext_matched)}")
    print(f"  Σ baixa.valor (postado no CA)             = {fmt(sum_baixa)}")
    diff_caixa = round(sum_baixa - ext_matched, 2)
    print(f"  >> DIFF CAIXA (baixa − extrato)           = {fmt(diff_caixa)}   "
          f"{'FECHA AO CENTAVO ✓' if abs(diff_caixa) < 0.01 else 'sobra excesso de subsídio (ver abaixo)'}")

    print(f"\n  --- RESÍDUO: agora EXPLÍCITO como ajuste, não drift escondido ---")
    print(f"  Σ recebível (net venda calc. processor)   = {fmt(recv_total)}")
    print(f"  Σ ajuste (recebível − crédito real)       = {fmt(sum_ajuste)}   <- o 'erro' do processor, AGORA VISÍVEL")
    print(f"  identidade: Σbaixa + Σajuste = Σrecebível consumido? "
          f"{fmt(round(sum_baixa + sum_ajuste, 2))} (refs casados)")

    print(f"\n  --- EXCEÇÕES (o portão que TRAVA, não acumula) ---")
    print(f"  nunca_baixou (recebível sem liberação = cancela-antes-liberar/boundary): "
          f"{len(result.nunca_baixou)} parcelas, {fmt(nunca_total)}")
    print(f"  sem_parcela  (liberação sem recebível = venda aprovada antes da janela): "
          f"{len(result.sem_parcela)} linhas, {fmt(ext_sem_parcela)}")

    print(f"\n  --- caixa por mês (baixa reproduz o extrato?) ---")
    print(f"  {'mês':<9}{'Σ extrato lib':>16}{'Σ baixa CA':>16}{'diff':>12}")
    for mm in sorted(set(baixa_by_month) | set(ext_by_month)):
        e_ = ext_by_month.get(mm, 0.0); b_ = baixa_by_month.get(mm, 0.0)
        print(f"  {mm:<9}{fmt(e_)}{fmt(b_)}{fmt(round(b_ - e_, 2))}")

    # exemplos reais de baixa com ajuste != 0
    com_ajuste = sorted([b for b in result.baixas if abs(b.ajuste) >= 0.01],
                        key=lambda b: -abs(b.ajuste))
    print(f"\n  --- amostra de baixas COM ajuste (top 5 de {len(com_ajuste)}) ---")
    for b in com_ajuste[:5]:
        print(f"    payment={b.payment_id:<14} data={b.data_pagamento} "
              f"valor={fmt(b.valor)} ajuste={fmt(b.ajuste)}")
    return diff_caixa, sum_ajuste


async def main():
    slugs = [sys.argv[1]] if len(sys.argv) > 1 else ["141air", "net-air"]
    for slug in slugs:
        await analyze(slug)
        print()


if __name__ == "__main__":
    asyncio.run(main())
