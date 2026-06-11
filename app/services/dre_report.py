"""DRE por competência a partir do event ledger (payment_events).

Convenção contábil (REGRAS_NEGOCIO 11.13):
  - receita/comissão/frete/subsídio/ajustes: mês de `competencia_date` (data da venda BRT)
  - devoluções (refund_created, partial_refund, charged_back): mês de `event_date`
    (data REAL do estorno) — diferente do painel ML, que conta no mês da venda.
    A diferença entre as duas é a "devolução diferida" (ver pontes.py), explicável
    porque cada row do ledger carrega AS DUAS datas.
"""
from collections import defaultdict

from app.db.supabase import get_db

# event_type -> (bucket do DRE, sinal de exibição)
_BUCKETS = {
    "sale_approved":      ("receita_bruta", +1),
    "reimbursed":         ("receita_bruta", +1),   # chargeback coberto pelo ML
    "fee_charged":        ("comissao", -1),        # signed_amount já é negativo
    "shipping_charged":   ("frete", -1),
    "subsidy_credited":   ("subsidio", +1),
    "refund_fee":         ("estorno_taxa", +1),
    "refund_shipping":    ("estorno_frete", +1),
    "adjustment_fee":     ("ajustes", +1),         # bidirecional (sinal vem do signed)
    "adjustment_shipping": ("ajustes", +1),
    "refund_created":     ("devolucoes", -1),
    "partial_refund":     ("devolucoes", -1),
    "charged_back":       ("devolucoes", -1),
}
# Eventos do CICLO DE DEVOLUÇÃO: bucketados pelo mês do ESTORNO (event_date).
# Inclui os estornos de taxa/frete — acompanham a devolução, não a venda.
_REFUND_CYCLE = {"refund_created", "partial_refund", "charged_back",
                 "refund_fee", "refund_shipping"}
_REFUND_TYPES = {"refund_created", "partial_refund", "charged_back"}


async def build_dre_monthly(seller_slug: str, date_from: str, date_to: str) -> dict:
    """DRE mensal por competência. Devoluções bucketadas pelo mês do ESTORNO
    (event_date); demais pelo mês da venda (competencia_date).

    Nota de janela: refunds de vendas aprovadas ANTES de date_from não aparecem
    (a query filtra por competencia_date) — use janela com folga pra trás.
    """
    db = get_db()
    dre: dict[str, dict] = defaultdict(lambda: defaultdict(float))
    page = 0
    while True:
        rows = db.table("payment_events").select(
            "event_type, signed_amount, competencia_date, event_date"
        ).eq("seller_slug", seller_slug).gte(
            "competencia_date", date_from
        ).lte("competencia_date", date_to).range(
            page * 1000, page * 1000 + 999
        ).execute()
        data = rows.data or []
        for r in data:
            et = r["event_type"]
            if et not in _BUCKETS:
                continue
            bucket, _sign = _BUCKETS[et]
            amount = float(r.get("signed_amount") or 0)
            if et in _REFUND_CYCLE:
                mes = (r.get("event_date") or r.get("competencia_date") or "")[:7]
                if et in _REFUND_TYPES:
                    dre[mes]["devolucoes"] += -amount  # signed negativo -> exibe positivo
                else:
                    dre[mes][bucket] += amount         # estorno_taxa/frete (+) no mês do estorno
            else:
                mes = (r.get("competencia_date") or "")[:7]
                if bucket in ("comissao", "frete"):
                    dre[mes][bucket] += -amount        # exibe positivo (despesa)
                else:
                    dre[mes][bucket] += amount
        if len(data) < 1000:
            break
        page += 1

    out = {}
    for mes in sorted(dre):
        v = dre[mes]
        resultado = (v.get("receita_bruta", 0) - v.get("devolucoes", 0)
                     - v.get("comissao", 0) - v.get("frete", 0)
                     + v.get("subsidio", 0) + v.get("estorno_taxa", 0)
                     + v.get("estorno_frete", 0) + v.get("ajustes", 0))
        out[mes] = {k: round(val, 2) for k, val in v.items()}
        out[mes]["resultado_vendas"] = round(resultado, 2)
    return out
