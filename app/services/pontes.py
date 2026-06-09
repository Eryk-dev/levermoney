"""Pontes de reconciliação a partir do event ledger.

Ponte 1 — caixa↔DRE:  Caixa do mês = DRE do mês ± Δ recebíveis a liberar.
  caixa = Σ cash_* events por mês (extrato ingerido no ledger); quando o mês não
  tem cash events, reporta caixa=null (extrato não ingerido) em vez de 0 enganoso.

Ponte 2 — DRE↔painel ML: o driver dominante é a DEVOLUÇÃO DIFERIDA — painel conta
  a devolução no mês da VENDA (competencia_date), o DRE no mês do ESTORNO
  (event_date). Cada row de refund no ledger carrega as duas datas → a ponte sai
  sem join: Σ refunds com competencia-mês ≠ estorno-mês, agrupado dos dois lados.
"""
from collections import defaultdict

from app.db.supabase import get_db
from app.services.dre_report import build_dre_monthly

_REFUND_TYPES = ("refund_created", "partial_refund", "charged_back")


def _caixa_por_mes(seller_slug: str, date_from: str, date_to: str) -> dict[str, float]:
    """Σ cash_* events (extrato ingerido) por mês de event_date."""
    db = get_db()
    out: dict[str, float] = defaultdict(float)
    page = 0
    while True:
        rows = db.table("payment_events").select(
            "event_type, signed_amount, event_date"
        ).eq("seller_slug", seller_slug).like("event_type", "cash_%").gte(
            "event_date", date_from
        ).lte("event_date", date_to).range(page * 1000, page * 1000 + 999).execute()
        data = rows.data or []
        for r in data:
            mes = (r.get("event_date") or "")[:7]
            out[mes] += float(r.get("signed_amount") or 0)
        if len(data) < 1000:
            break
        page += 1
    return {m: round(v, 2) for m, v in out.items()}


def _devolucao_diferida(seller_slug: str, date_from: str, date_to: str) -> dict:
    """Refunds com mês de estorno ≠ mês da venda.

    Retorna {saiu_do_mes: {mes_venda: valor}, entrou_no_mes: {mes_estorno: valor}}:
    o painel ML conta no mes_venda; o DRE conta no mes_estorno.
    """
    db = get_db()
    saiu = defaultdict(float)    # painel tem, DRE daquele mês não
    entrou = defaultdict(float)  # DRE tem, painel daquele mês não
    page = 0
    while True:
        rows = db.table("payment_events").select(
            "event_type, signed_amount, competencia_date, event_date"
        ).eq("seller_slug", seller_slug).in_("event_type", list(_REFUND_TYPES)).gte(
            "competencia_date", date_from
        ).lte("competencia_date", date_to).range(page * 1000, page * 1000 + 999).execute()
        data = rows.data or []
        for r in data:
            venda_m = (r.get("competencia_date") or "")[:7]
            estorno_m = (r.get("event_date") or venda_m or "")[:7]
            if venda_m and estorno_m and venda_m != estorno_m:
                val = -float(r.get("signed_amount") or 0)  # signed negativo -> valor positivo
                saiu[venda_m] += val
                entrou[estorno_m] += val
        if len(data) < 1000:
            break
        page += 1
    return {"saiu_do_mes": {m: round(v, 2) for m, v in saiu.items()},
            "entrou_no_mes": {m: round(v, 2) for m, v in entrou.items()}}


async def build_pontes(seller_slug: str, date_from: str, date_to: str) -> dict:
    dre = await build_dre_monthly(seller_slug, date_from, date_to)
    caixa = _caixa_por_mes(seller_slug, date_from, date_to)
    diferida = _devolucao_diferida(seller_slug, date_from, date_to)

    meses = sorted(set(dre) | set(caixa))
    ponte_caixa_dre = {}
    for m in meses:
        res = (dre.get(m) or {}).get("resultado_vendas", 0.0)
        cx = caixa.get(m)
        ponte_caixa_dre[m] = {
            "dre_resultado": res,
            "caixa": cx,  # null = extrato não ingerido no ledger nesse mês
            "delta_recebiveis": round(res - cx, 2) if cx is not None else None,
        }

    return {
        "seller": seller_slug, "de": date_from, "ate": date_to,
        "dre": dre,
        "ponte_caixa_dre": ponte_caixa_dre,
        "ponte_dre_painel_ml": {
            "devolucao_diferida": diferida,
            "nota": ("painel ML conta devolução no mês da VENDA; DRE no mês do ESTORNO. "
                     "painel_mes ≈ dre_devolucoes_mes - entrou_no_mes + saiu_do_mes "
                     "(+ by_admin/kit-split, subsídio, financing_fee — ver REGRAS_NEGOCIO 11.13)"),
        },
    }
