"""
Financial closing endpoints: trigger, status, seller detail.
"""
from fastapi import APIRouter, Depends, Query

from app.db.supabase import get_db
from app.models.sellers import get_seller_config
from app.services import caixa_judge
from app.services.financial_closing import (
    compute_seller_financial_closing,
    get_last_financial_closing,
    run_financial_closing_for_all,
)
from ._deps import require_admin

router = APIRouter()


# ── Juiz de Caixa (portão P1) ────────────────────────────────

@router.get("/caixa-judge/{seller_slug}", dependencies=[Depends(require_admin)])
async def caixa_judge_seller(
    seller_slug: str,
    data_de: str = Query(..., description="YYYY-MM-DD"),
    data_ate: str = Query(..., description="YYYY-MM-DD"),
):
    """Reconciliação de VALOR diária: extrato MP (âncora + por dia) vs CA
    (saldo absoluto + baixas por data_pagamento). Leitura pura, não escreve."""
    seller = get_seller_config(get_db(), seller_slug)
    if not seller:
        return {"error": f"Seller {seller_slug} not found"}
    return await caixa_judge.judge_seller(seller_slug, data_de, data_ate, seller)


# ── Financial Closing ────────────────────────────────────────

@router.post("/closing/trigger", dependencies=[Depends(require_admin)])
async def trigger_financial_closing(
    date_from: str | None = Query(None, description="YYYY-MM-DD"),
    date_to: str | None = Query(None, description="YYYY-MM-DD"),
):
    return await run_financial_closing_for_all(date_from=date_from, date_to=date_to)


@router.get("/closing/status", dependencies=[Depends(require_admin)])
async def financial_closing_status():
    return get_last_financial_closing()


@router.get("/closing/seller/{seller_slug}", dependencies=[Depends(require_admin)])
async def financial_closing_seller(
    seller_slug: str,
    date_from: str | None = Query(None, description="YYYY-MM-DD"),
    date_to: str | None = Query(None, description="YYYY-MM-DD"),
):
    return await compute_seller_financial_closing(
        seller_slug=seller_slug,
        date_from=date_from,
        date_to=date_to,
    )
