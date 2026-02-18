"""
Job de Baixas: processa baixas de parcelas com vencimento <= hoje.

Despesas (comissao, frete) e receitas sao criadas SEM baixa pelo processor.
Este job roda separadamente e faz a baixa quando money_release_date <= hoje.

Antes de cada baixa, verifica money_release_status == "released" no ML
(via Supabase cache + fallback API ML) para evitar baixas prematuras.

GET /baixas/processar/{seller_slug}?dry_run=true  (preview)
GET /baixas/processar/{seller_slug}?dry_run=false (executa)
"""
import asyncio
import logging
from datetime import datetime, timedelta

from fastapi import APIRouter, Query

from app.db.supabase import get_db
from app.models.sellers import get_seller_config
from app.services import ca_api, ca_queue
from app.services.release_checker import ReleaseChecker, _is_refund_or_estorno

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/baixas")

# Start date for searching open parcelas (far enough to catch all)
DEFAULT_LOOKBACK_DAYS = 90


@router.get("/processar/{seller_slug}")
async def processar_baixas(
    seller_slug: str,
    dry_run: bool = Query(True, description="Se True, apenas lista sem criar baixas"),
    verify_release: bool = Query(True, description="Verifica money_release_status no ML antes da baixa"),
    data_ate: str = Query(None, description="Data limite (default=hoje, YYYY-MM-DD)"),
    lookback_days: int = Query(DEFAULT_LOOKBACK_DAYS, description="Dias para trás na busca"),
):
    """
    Busca parcelas abertas (EM_ABERTO/ATRASADO) com vencimento <= data_ate
    na conta bancária do seller e cria baixa para cada uma.

    Com verify_release=true (default), verifica no ML se o dinheiro foi
    realmente liberado antes de dar baixa. Parcelas cujo payment ainda está
    "pending" são puladas e reportadas separadamente.
    """
    db = get_db()
    seller = get_seller_config(db, seller_slug)
    if not seller:
        return {"error": f"Seller {seller_slug} not found"}

    today = datetime.now().strftime("%Y-%m-%d")
    data_ate = data_ate or today
    data_de = (datetime.now() - timedelta(days=lookback_days)).strftime("%Y-%m-%d")
    conta_bancaria = seller["ca_conta_bancaria"]

    # Fetch all open parcelas (paginate if needed)
    parcelas_pagar = await _fetch_all_parcelas(
        ca_api.buscar_parcelas_abertas_pagar, conta_bancaria, data_de, data_ate
    )
    parcelas_receber = await _fetch_all_parcelas(
        ca_api.buscar_parcelas_abertas_receber, conta_bancaria, data_de, data_ate
    )

    # Release verification
    skipped_pagar = []
    skipped_receber = []
    release_map = {}

    if verify_release and (parcelas_pagar or parcelas_receber):
        checker = ReleaseChecker(seller_slug)
        release_map = await checker.check_parcelas_batch(parcelas_pagar + parcelas_receber)

        parcelas_pagar, skipped_pagar = _split_by_release(parcelas_pagar, release_map)
        parcelas_receber, skipped_receber = _split_by_release(parcelas_receber, release_map)

    if dry_run:
        resp = {
            "mode": "dry_run",
            "seller": seller_slug,
            "data_de": data_de,
            "data_ate": data_ate,
            "conta_bancaria": conta_bancaria,
            "verify_release": verify_release,
            "parcelas_pagar": {
                "total": len(parcelas_pagar),
                "itens": [_summarize(p, release_map) for p in parcelas_pagar],
            },
            "parcelas_receber": {
                "total": len(parcelas_receber),
                "itens": [_summarize(p, release_map) for p in parcelas_receber],
            },
        }
        if verify_release:
            resp["skipped_pagar"] = {
                "total": len(skipped_pagar),
                "motivo": "money_release_status != released",
                "itens": [_summarize(p, release_map) for p in skipped_pagar],
            }
            resp["skipped_receber"] = {
                "total": len(skipped_receber),
                "motivo": "money_release_status != released",
                "itens": [_summarize(p, release_map) for p in skipped_receber],
            }
        return resp

    # === PROCESS MODE ===
    results_pagar = await _processar_baixas_lista(parcelas_pagar, seller_slug, conta_bancaria, "pagar")
    results_receber = await _processar_baixas_lista(parcelas_receber, seller_slug, conta_bancaria, "receber")

    resp = {
        "mode": "process",
        "seller": seller_slug,
        "data_de": data_de,
        "data_ate": data_ate,
        "verify_release": verify_release,
        "pagar": {
            "total": len(parcelas_pagar),
            "queued": sum(1 for r in results_pagar if r["status"] == "queued"),
            "errors": sum(1 for r in results_pagar if r["status"] == "error"),
            "results": results_pagar,
        },
        "receber": {
            "total": len(parcelas_receber),
            "queued": sum(1 for r in results_receber if r["status"] == "queued"),
            "errors": sum(1 for r in results_receber if r["status"] == "error"),
            "results": results_receber,
        },
    }
    if verify_release:
        resp["skipped_pagar"] = {
            "total": len(skipped_pagar),
            "motivo": "money_release_status != released",
            "itens": [_summarize(p, release_map) for p in skipped_pagar],
        }
        resp["skipped_receber"] = {
            "total": len(skipped_receber),
            "motivo": "money_release_status != released",
            "itens": [_summarize(p, release_map) for p in skipped_receber],
        }
    return resp


def _split_by_release(parcelas: list[dict], release_map: dict[str, str]) -> tuple[list, list]:
    """Split parcelas into (ok, skipped) based on release_map status."""
    ok = []
    skipped = []
    for p in parcelas:
        status = release_map.get(p.get("id", ""))
        if status in ("released", "bypass"):
            ok.append(p)
        elif status == "unknown":
            # Can't determine release status — conservative: allow baixa
            # (these are parcelas we couldn't match to a payment)
            ok.append(p)
        else:
            skipped.append(p)
    return ok, skipped


async def _fetch_all_parcelas(search_fn, conta_id: str, data_de: str, data_ate: str) -> list:
    """Paginate through all results from a CA search endpoint."""
    all_items = []
    pagina = 1
    page_size = 50

    while True:
        items, total = await search_fn(conta_id, data_de, data_ate, pagina, page_size)
        all_items.extend(items)

        if len(all_items) >= total or not items:
            break

        pagina += 1
        await asyncio.sleep(0.3)

    return all_items


async def _processar_baixas_lista(parcelas: list, seller_slug: str, conta_financeira: str, tipo: str) -> list:
    """Enqueue baixa for each open parcela."""
    results = []

    for p in parcelas:
        parcela_id = p["id"]
        descricao = p.get("descricao", "")
        data_vencimento = p.get("data_vencimento", "")
        valor = p.get("nao_pago", p.get("total", 0))

        payload = {
            "data_pagamento": data_vencimento,
            "composicao_valor": {"valor_bruto": valor},
            "conta_financeira": conta_financeira,
        }

        try:
            await ca_queue.enqueue_baixa(seller_slug, parcela_id, payload, scheduled_for=None)
            results.append({
                "id": parcela_id,
                "tipo": tipo,
                "descricao": descricao,
                "valor": valor,
                "data_vencimento": data_vencimento,
                "status": "queued",
            })
            logger.info(f"Baixa enqueued ({tipo}): {descricao} R${valor} venc={data_vencimento}")
        except Exception as e:
            results.append({
                "id": parcela_id,
                "tipo": tipo,
                "descricao": descricao,
                "valor": valor,
                "data_vencimento": data_vencimento,
                "status": "error",
                "error": str(e),
            })
            logger.error(f"Baixa enqueue FAILED ({tipo}): {descricao} R${valor}: {e}")

    return results


async def processar_baixas_auto(seller_slug: str) -> dict:
    """Reusable function called by the daily scheduler.
    Fetches open parcelas and enqueues baixas for all with vencimento <= today.
    Verifies money_release_status before each baixa."""
    db = get_db()
    seller = get_seller_config(db, seller_slug)
    if not seller:
        logger.error(f"processar_baixas_auto: seller {seller_slug} not found")
        return {"error": f"Seller {seller_slug} not found"}

    today = datetime.now().strftime("%Y-%m-%d")
    data_de = (datetime.now() - timedelta(days=DEFAULT_LOOKBACK_DAYS)).strftime("%Y-%m-%d")
    conta_bancaria = seller["ca_conta_bancaria"]

    parcelas_pagar = await _fetch_all_parcelas(
        ca_api.buscar_parcelas_abertas_pagar, conta_bancaria, data_de, today
    )
    parcelas_receber = await _fetch_all_parcelas(
        ca_api.buscar_parcelas_abertas_receber, conta_bancaria, data_de, today
    )

    # Release verification
    checker = ReleaseChecker(seller_slug)
    release_map = await checker.check_parcelas_batch(parcelas_pagar + parcelas_receber)

    parcelas_pagar, skipped_pagar = _split_by_release(parcelas_pagar, release_map)
    parcelas_receber, skipped_receber = _split_by_release(parcelas_receber, release_map)

    skipped_total = len(skipped_pagar) + len(skipped_receber)
    if skipped_total:
        logger.info(f"processar_baixas_auto({seller_slug}): skipped {skipped_total} parcelas (not released)")

    results_pagar = await _processar_baixas_lista(parcelas_pagar, seller_slug, conta_bancaria, "pagar")
    results_receber = await _processar_baixas_lista(parcelas_receber, seller_slug, conta_bancaria, "receber")

    total_queued = (
        sum(1 for r in results_pagar if r["status"] == "queued") +
        sum(1 for r in results_receber if r["status"] == "queued")
    )
    logger.info(f"processar_baixas_auto({seller_slug}): {total_queued} baixas enqueued, {skipped_total} skipped")
    return {"seller": seller_slug, "queued": total_queued, "skipped": skipped_total}


def _summarize(parcela: dict, release_map: dict[str, str] | None = None) -> dict:
    """Summarize a parcela for dry_run output."""
    summary = {
        "id": parcela.get("id"),
        "descricao": parcela.get("descricao"),
        "data_vencimento": parcela.get("data_vencimento"),
        "total": parcela.get("total"),
        "nao_pago": parcela.get("nao_pago"),
        "status": parcela.get("status_traduzido", parcela.get("status")),
    }
    if release_map:
        summary["release_status"] = release_map.get(parcela.get("id", ""), "n/a")
    return summary
