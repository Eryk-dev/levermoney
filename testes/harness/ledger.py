"""JUIZ per-ref full-ledger: processor + extrato_ingester juntos vs extrato, POR REF.

Diferente do coverage.py (soma-total, confundido por boundary/classifier), aqui cada
ref de venda é reconciliado isolado: CA_ref = Σ processor(assinado) + Σ ingester(face).
Linha ingerida = valor do extrato -> cancela. Resíduo só sobra de: gap (ninguém cobre),
double-count (processor E ingester cobrem a mesma linha), ou boundary (perna fora da
janela de dados jan-mai = artefato, não bug).

Separa resíduo IN-WINDOW (lifecycle todo em jan-mai = bug real e eliminável) de
BOUNDARY (perna em dez/jun = artefato de janela, some em produção contínua).

Flags: --fix2 (ingere debito_envio_ml) --fix3 (dedup reembolso vs estorno_taxa do processor)

Uso: python3 -m testes.harness.ledger 141air [--fix2] [--fix3]
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
EXTRATO_MAP = {
    ("141air", "jan"): "extratos/extrato janeiro 141Air.csv", ("141air", "fev"): "extratos/extrato fevereiro 141Air.csv",
    ("141air", "mar"): "extratos/extrato marco 141Air.csv", ("141air", "abr"): "extratos/extrato abril 141Air.csv",
    ("141air", "mai"): "extratos/extrato maio 141Air.csv",
    ("net-air", "jan"): "extratos/extrato janeiro netair.csv", ("net-air", "fev"): "extratos/extrato fevereiro netair.csv",
    ("net-air", "mar"): "extratos/extrato marco netair.csv", ("net-air", "abr"): "extratos/extrato abril netair.csv",
    ("net-air", "mai"): "extratos/extrato maio netair.csv",
}
WIN_LO, WIN_HI = "2026-01", "2026-05"
ALWAYS_INGEST = {"reembolso_disputa", "reembolso_generico", "entrada_dinheiro",
                 "dinheiro_retido", "liberacao_cancelada"}
FEE_REFUND_TYPES = {"reembolso_disputa", "reembolso_generico", "entrada_dinheiro"}


def fmt(v):
    return f"{v:>13,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def brt_month(iso):
    try:
        return datetime.fromisoformat(iso).astimezone(timezone(timedelta(hours=-3))).strftime("%Y-%m")
    except (ValueError, TypeError):
        return iso[:7] if iso else ""


async def main():
    slug = sys.argv[1] if len(sys.argv) > 1 else "141air"
    fix2 = "--fix2" in sys.argv
    fix3 = "--fix3" in sys.argv
    noet = "--noet" in sys.argv   # simula remover estorno_taxa do processor
    fix4 = "--fix4" in sys.argv
    always = set(ALWAYS_INGEST) | ({"debito_envio_ml"} if fix2 else set())
    if fix4:
        always.discard("entrada_dinheiro")  # pix/entrada em ref de venda = a própria liberação

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

    proc_by_ref = defaultdict(float)
    venc_out_ref = defaultdict(bool)
    sale_refs = set()
    refunded_refs = set()       # tem estorno (full refund -> tem estorno_taxa)
    estorno_taxa_ref = defaultdict(float)
    for e in cap.events:
        base = e.payment_id.split("_")[0]
        proc_by_ref[base] += SIGN.get(e.tipo, 0.0) * e.valor
        venc = (e.vencimento or "")[:7]
        if venc and not (WIN_LO <= venc <= WIN_HI):
            venc_out_ref[base] = True
        if e.tipo == "receita" and not e.payment_id.endswith("_subsidy"):
            sale_refs.add(base)
        if e.tipo in ("estorno", "partial_refund"):
            refunded_refs.add(base)
        if e.tipo == "estorno_taxa":
            estorno_taxa_ref[base] += e.valor

    # extrato por ref
    ext_lines_ref = defaultdict(list)   # ref -> [(etype, net, raw_type)]
    ext_total_ref = defaultdict(float)
    for m in MONTHS:
        ep = EXTRATO_MAP.get((slug, m))
        if not ep or not os.path.exists(os.path.join(BASE, ep)):
            continue
        _, rows = judge.load_extrato(os.path.join(BASE, ep))
        for r in rows:
            ref = str(r["ref"])
            etype, direction, code, pat = judge.classify(r["type"])
            ext_lines_ref[ref].append((etype, r["net"], r["type"]))
            ext_total_ref[ref] += r["net"]

    # universo: refs de venda PRESENTES no extrato (matched) — como o diag.
    matched = [r for r in sale_refs if r in ext_lines_ref]
    no_extrato = [r for r in sale_refs if r not in ext_lines_ref]
    res_no_ext = sum(proc_by_ref[r] for r in no_extrato)  # release fora janela / ref-id != payment

    res_inwin = 0.0
    res_bound = 0.0
    absdiff_inwin = 0.0
    worst = []
    n_inwin_off = 0
    fix3b = "--fix3b" in sys.argv
    for ref in matched:
        proc = proc_by_ref[ref] - (estorno_taxa_ref[ref] if noet else 0.0)
        ingested = 0.0
        for etype, net, raw in ext_lines_ref.get(ref, []):
            if etype == "__SALE__" or etype is None or etype == "__OTHER__":
                continue  # liberação/skip/other: coberto pelo processor (ou cauda)
            if etype == "debito_divida_disputa":
                if ref in refunded_refs:
                    continue  # deduped: processor estornou
                ingested += net
                continue
            if fix3 and etype in FEE_REFUND_TYPES and ref in refunded_refs:
                continue  # dedup: estorno_taxa do processor já reverteu a taxa
            # fix3b: dedup SÓ as linhas de refund-de-TAXA vs estorno_taxa do processor.
            # entrada_dinheiro / reembolso de tarifas / reembolso envio cancelado = taxa.
            # "reembolso reclamações" = liberação de retido (pareia c/ retido) -> NÃO dedup.
            if fix3b and ref in refunded_refs:
                nraw = judge.norm(raw)
                is_fee_refund = (
                    etype == "entrada_dinheiro"
                    or etype == "reembolso_generico"
                    or (etype == "reembolso_disputa" and "envio cancelado" in nraw)
                )
                if is_fee_refund:
                    continue
            if etype in always:
                ingested += net
                continue
            continue  # else: ingester pula (already_covered)
        ca = proc + ingested
        d = ext_total_ref[ref] - ca
        dapp = brt_month(merged.get(ref, {}).get("date_approved") or merged.get(ref, {}).get("date_created", ""))
        rel_m = brt_month(merged.get(ref, {}).get("money_release_date", ""))
        is_bound = venc_out_ref[ref] or (dapp and not (WIN_LO <= dapp <= WIN_HI)) \
            or (rel_m and not (WIN_LO <= rel_m <= WIN_HI))
        if is_bound:
            res_bound += d
        else:
            res_inwin += d
            absdiff_inwin += abs(d)
            if abs(d) > 0.5:
                n_inwin_off += 1
                st = (merged.get(ref, {}) or {}).get("status")
                worst.append((abs(d), ref, ext_total_ref[ref], ca, st))
    worst.sort(reverse=True)

    tag = ("+fix2" if fix2 else "") + ("+fix3" if fix3 else "") or "BASE"
    print("=" * 80)
    print(f"LEDGER per-ref (processor+ingester) — {slug} [{tag}]")
    print("=" * 80)
    print(f"  refs de venda: {len(sale_refs)}  (matched no extrato={len(matched)}, sem-extrato={len(no_extrato)})")
    print(f"  >>> RESÍDUO IN-WINDOW matched (lifecycle todo jan-mai = bug real) = {fmt(res_inwin)}")
    print(f"      Σ|resíduo| in-window = {fmt(absdiff_inwin)} | refs off>R$0,50: {n_inwin_off}")
    print(f"  resíduo BOUNDARY matched (perna dez/jun = artefato de janela)     = {fmt(res_bound)}")
    print(f"  resíduo SEM-EXTRATO (release fora janela / ref-id != payment_id)  = {fmt(res_no_ext)}")
    if worst:
        print(f"  piores IN-WINDOW (|d|, ref, extrato, CA_full, status):")
        for d, ref, e, c, st in worst[:10]:
            print(f"    {fmt(d)}  ref={ref:<14} ext={fmt(e)} ca={fmt(c)} [{st}]")


if __name__ == "__main__":
    asyncio.run(main())
