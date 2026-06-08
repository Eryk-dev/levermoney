"""Fase 3-full — wiring da baixa extrato-dirigida ao CA real.
Mantém baixas_extrato.py PURO. Aqui: download do extrato, lookup de parcelas no CA,
planejamento, e (com flag baixa_extrato_write_sellers) postagem via ca_queue."""
import logging
import re

from app.config import settings
from app.services import ca_api, ca_queue
from app.services.baixas_extrato import plan_baixas_from_extrato
from app.services.extrato_ingester import _normalize_report_bytes, _parse_account_statement
from app.services.release_report_sync import _get_or_create_report

logger = logging.getLogger(__name__)

_PID_RE = re.compile(r"(?:Payment|#)\s*(\d{6,})")


def _payment_id_from_parcela(parcela: dict) -> str | None:
    m = _PID_RE.search(parcela.get("descricao", "") or "")
    return m.group(1) if m else None


def _ddmmyyyy_to_iso(d: str) -> str:
    p = (d or "").split("-")
    return f"{p[2]}-{p[1]}-{p[0]}" if len(p) == 3 else d


async def _fetch_open_parcelas(search_fn, conta_id: str, data_de: str, data_ate: str) -> list:
    """Pagina buscar_parcelas_abertas_* e normaliza para {id, descricao, nao_pago}."""
    out, page = [], 1
    while True:
        itens, total = await search_fn(conta_id, data_de, data_ate, pagina=page, tamanho=50)
        for it in itens:
            for parc in it.get("parcelas", [it]):
                out.append({"id": str(parc.get("id")), "descricao": it.get("descricao", ""),
                            "nao_pago": float(parc.get("nao_pago", parc.get("valor", 0)) or 0)})
        if len(out) >= total or not itens:
            break
        page += 1
    return out


async def plan_for_seller(seller_slug: str, data_de: str, data_ate: str, seller: dict):
    conta = seller["ca_conta_bancaria"]
    report = await _get_or_create_report(seller_slug, data_de, data_ate)
    summary, txs = _parse_account_statement(_normalize_report_bytes(report).decode("utf-8"))
    extrato_lines = [
        {"ref": str(t["reference_id"]), "net": t["amount"], "date": t["date"]}
        for t in txs
    ]
    parcelas = []
    for fn in (ca_api.buscar_parcelas_abertas_receber, ca_api.buscar_parcelas_abertas_pagar):
        raw = await _fetch_open_parcelas(fn, conta, data_de, data_ate)
        for p in raw:
            pid = _payment_id_from_parcela(p)
            if pid:
                p["payment_id"] = pid
                parcelas.append(p)
    return plan_baixas_from_extrato(extrato_lines, parcelas)


async def run_for_seller(seller_slug: str, data_de: str, data_ate: str, seller: dict) -> dict:
    plan = await plan_for_seller(seller_slug, data_de, data_ate, seller)
    write_on = seller_slug in {s.strip() for s in settings.baixa_extrato_write_sellers.split(",") if s.strip()}
    posted = 0
    for b in plan.baixas:
        if write_on:
            payload = {"data_pagamento": b.data_pagamento,
                       "composicao_valor": {"valor_bruto": b.valor},
                       "conta_financeira": seller["ca_conta_bancaria"]}
            await ca_queue.enqueue_baixa(seller_slug, b.parcela_id, payload, scheduled_for=None)
            posted += 1
        else:
            logger.info("[dry-run] baixa %s payment=%s data=%s valor=%.2f ajuste=%.2f",
                        b.parcela_id, b.payment_id, b.data_pagamento, b.valor, b.ajuste)
    logger.info("baixas_extrato %s: %d planejadas, %d postadas, %d nunca_baixou, %d sem_parcela",
                seller_slug, len(plan.baixas), posted, len(plan.nunca_baixou), len(plan.sem_parcela))
    return {"seller": seller_slug, "planejadas": len(plan.baixas), "postadas": posted,
            "nunca_baixou": len(plan.nunca_baixou), "sem_parcela": len(plan.sem_parcela), "write": write_on}
