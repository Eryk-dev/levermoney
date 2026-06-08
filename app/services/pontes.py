"""Pontes de reconciliação: caixa↔DRE (recebíveis a liberar) e DRE↔painel ML (devolução diferida)."""
from collections import defaultdict
from app.services.dre_report import _brt_month


def ponte_caixa_dre(dre: dict, caixa_por_mes: dict) -> dict:
    out = {}
    for m in set(dre) | set(caixa_por_mes):
        res = (dre.get(m, {}) or {}).get("resultado_vendas", 0.0)
        cx = caixa_por_mes.get(m, 0.0)
        out[m] = {"dre_resultado": res, "caixa": cx, "delta_receberveis": round(res - cx, 2)}
    return out


def devolucao_diferida(payments: list[dict]) -> dict:
    diff = defaultdict(float)
    for p in payments:
        raw = p.get("raw_payment") or {}
        if p.get("ml_status") in ("refunded", "charged_back") and raw.get("status_detail") != "reimbursed":
            venda_m = _brt_month(raw.get("date_approved") or raw.get("date_created", ""))
            estorno_m = _brt_month(raw.get("date_last_updated") or raw.get("date_approved", ""))
            if venda_m and estorno_m and venda_m != estorno_m:
                val = min(float(raw.get("transaction_amount_refunded") or p.get("amount") or 0),
                          float(p.get("amount") or 0))
                diff[estorno_m] += val
    return dict(diff)
