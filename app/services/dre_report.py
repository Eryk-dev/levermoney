"""DRE por competência a partir da tabela payments (produção).
Receita bruta por date_approved (BRT); devolução por data do estorno (date_last_updated BRT)."""
from collections import defaultdict
from datetime import datetime, timezone, timedelta

BRT = timezone(timedelta(hours=-3))


def _brt_month(iso: str) -> str:
    try:
        return datetime.fromisoformat(iso).astimezone(BRT).strftime("%Y-%m")
    except (ValueError, TypeError):
        return (iso or "")[:7]


def build_dre_from_payments(payments: list[dict]) -> dict:
    dre = defaultdict(lambda: defaultdict(float))
    for p in payments:
        raw = p.get("raw_payment") or {}
        st = p.get("ml_status")
        venda_m = _brt_month(raw.get("date_approved") or raw.get("date_created", ""))
        amount = float(p.get("amount") or 0)
        fee = float(p.get("processor_fee") or 0)
        ship = float(p.get("processor_shipping") or 0)
        if st in ("approved", "in_mediation") or (st == "charged_back" and raw.get("status_detail") == "reimbursed"):
            dre[venda_m]["receita_bruta"] += amount
            dre[venda_m]["comissao"] += fee
            dre[venda_m]["frete"] += ship
        if st in ("refunded", "charged_back") and raw.get("status_detail") != "reimbursed":
            estorno_m = _brt_month(raw.get("date_last_updated") or raw.get("date_approved", ""))
            dre[estorno_m]["devolucoes"] += min(float(raw.get("transaction_amount_refunded") or amount), amount)
    for m, v in dre.items():
        v["resultado_vendas"] = v["receita_bruta"] - v["comissao"] - v["frete"] - v["devolucoes"]
    return {m: dict(v) for m, v in dre.items()}
