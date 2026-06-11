"""Reconstrução do SALDO dia a dia: extrato (verdade) vs CA reconstruído.

Premissa: TODO non-sale lançado manualmente pelo VALOR do extrato (o ingester usa o valor do
extrato), e vendas lançadas pelo processor REAL. Pergunta: o saldo do CA diverge do extrato?

Método honesto:
- Extrato = verdade. Saldo corrido = cumsum dos net em ordem de data (== PARTIAL_BALANCE,
  encadeado entre meses).
- CA reconstruído = extrato + Σ (erro de valor de VENDA por ref), o erro lançado na ÚLTIMA
  data de extrato daquele ref (quando o ciclo da venda fecha).
  - non-sale: valor do CA == valor do extrato -> contribui 0 pro diff (premissa).
  - venda: erro_ref = (net que o processor calculou) - (net do extrato pra esse ref).
- Separa boundary (venda com só uma perna na janela jan-mai) de erro REAL.

Uso: python3 -m testes.harness.saldo 141air
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
EMAP = {"141air": {"jan": "janeiro", "fev": "fevereiro", "mar": "marco", "abr": "abril", "mai": "maio"},
        "net-air": {"jan": "janeiro", "fev": "fevereiro", "mar": "marco", "abr": "abril", "mai": "maio"}}
EXTSL = {"141air": "141Air", "net-air": "netair"}


def fmt(v):
    return f"{v:>13,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def iso(d):  # DD-MM-YYYY -> YYYY-MM-DD
    p = d.split("-")
    return f"{p[2]}-{p[1]}-{p[0]}" if len(p) == 3 else d


async def main():
    slug = sys.argv[1] if len(sys.argv) > 1 else "141air"
    extsl = EXTSL[slug]

    # 1. processa cada payment 1x -> net do CA por ref + set de vendas
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
    ca_net = {}; sale = set()
    for e in cap.events:
        b = e.payment_id.split("_")[0]
        if e.tipo in ("receita", "comissao", "frete"):
            sale.add(b)
        ca_net[b] = ca_net.get(b, 0.0) + SIGN.get(e.tipo, 0.0) * e.valor

    # 2. extrato em ordem de data (todos os meses), + saldo inicial de jan
    lines = []  # (iso_date, ref, net)
    initial = None
    for m in MONTHS:
        path = os.path.join(BASE, "extratos", f"extrato {EMAP[slug][m]} {extsl}.csv")
        if not os.path.exists(path):
            continue
        header, rows = judge.load_extrato(path)
        if initial is None:
            initial = header["initial"]
        for r in rows:
            lines.append((iso(r["date"]), str(r["ref"]), r["net"]))
    lines.sort(key=lambda x: x[0])

    # 3. ALINHADO AO CÓDIGO: caixa de venda bucketado pela DATA DE BAIXA real (vencimento =
    #    money_release_date p/ receita, data do estorno p/ refund), igual o código faz.
    #    Assim a perna fora da janela (liberada em dez, libera em jun) é EXCLUÍDA dos DOIS
    #    lados de forma consistente -> sem boundary artificial.
    ext_sale_by_day = {}   # extrato: linhas de venda por data
    for d, ref, net in lines:
        if ref in sale:
            ext_sale_by_day[d] = ext_sale_by_day.get(d, 0.0) + net
    ca_sale_by_day = {}    # CA: eventos de venda por data de VENCIMENTO (baixa)
    skipped_no_venc = 0.0
    for e in cap.events:
        venc = (e.vencimento or "")[:10]
        val = SIGN.get(e.tipo, 0.0) * e.valor
        if not venc:
            skipped_no_venc += val
            continue
        ca_sale_by_day[venc] = ca_sale_by_day.get(venc, 0.0) + val

    # 4. reconstrução dia a dia (só janela jan-mai). non-sale cancela (lançado = extrato).
    win_lo, win_hi = "2026-01-01", "2026-05-31"
    all_days = sorted(set(ext_sale_by_day) | set(ca_sale_by_day))
    print("=" * 78)
    print(f"RECONSTRUÇÃO DE SALDO — {slug} (jan-mai/2026) — ALINHADO AO CÓDIGO")
    print(f"caixa de venda bucketado pela data de baixa (money_release_date/estorno), como o código")
    print("=" * 78)
    print(f"\n{'mês':<8}{'Σ extrato vendas':>18}{'Σ CA vendas':>16}{'diff mês':>14}{'diff acum':>14}")
    cum = 0.0
    month = {}
    for d in all_days:
        if d < win_lo or d > win_hi:
            continue
        e_ = ext_sale_by_day.get(d, 0.0)
        c_ = ca_sale_by_day.get(d, 0.0)
        mm = d[:7]
        m = month.setdefault(mm, [0.0, 0.0])
        m[0] += e_; m[1] += c_
    for mm in sorted(month):
        e_, c_ = month[mm]
        cum += (c_ - e_)
        print(f"{mm:<8}{fmt(e_)}{fmt(c_)}{fmt(c_-e_)}{fmt(cum)}")

    # quanto da diferença é venda que LIBERA FORA da janela (vencimento fora 2026-01..05)
    fora = 0.0
    for e in cap.events:
        venc = (e.vencimento or "")[:10]
        if venc and (venc < win_lo or venc > win_hi):
            fora += SIGN.get(e.tipo, 0.0) * e.valor
    print(f"\n>>> DIFF FINAL de CAIXA (jan-mai, vendas) = {fmt(cum)}")
    print(f"    (non-sale = 0, lançado ao valor do extrato)")
    print(f"    CA vendas com vencimento FORA da janela (liberadas dez/ ou jun+) = {fmt(fora)}")
    print(f"      -> essas reconciliam no SEU mês (dez ou jun), não em jan-mai. Sem boundary.")
    print(f"\n    Resíduo restante = erro de valor (refund parcial) + desalinho data money_release")
    print(f"    vs crédito real do extrato. Com baixa extrato-dirigida (valor+data do extrato),")
    print(f"    o CA vendas == extrato vendas por construção -> diff -> 0 ao centavo.")


if __name__ == "__main__":
    asyncio.run(main())
