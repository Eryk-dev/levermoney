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


# ── Lançador de complemento (lançamentos categorizados extrato-driven) ───────

@router.get("/complemento/{seller_slug}", dependencies=[Depends(require_admin)])
async def complemento_seller(
    seller_slug: str,
    data_de: str = Query(..., description="YYYY-MM-DD"),
    data_ate: str = Query(..., description="YYYY-MM-DD"),
    apply: bool = Query(False, description="True = posta via ca_queue (gated por baixa_extrato_write_sellers)"),
):
    """Política disputa=cancelamento: zera categorias da venda lançada + resultado
    real do banco categorizado (perda/ganho disputa, dívida ML, estorno parcial).
    Sem apply = só o plano (leitura pura)."""
    from app.services import complemento_runner
    seller = get_seller_config(get_db(), seller_slug)
    if not seller:
        return {"error": f"Seller {seller_slug} not found"}
    if apply:
        return await complemento_runner.run_for_seller(seller_slug, data_de, data_ate, seller)
    plan = await complemento_runner.plan_for_seller(seller_slug, data_de, data_ate)
    plan["complementos"] = [vars(c) for c in plan["complementos"]]
    return plan


# ── Retrofit do histórico de baixas ──────────────────────────

@router.get("/baixas-retrofit/{seller_slug}", dependencies=[Depends(require_admin)])
async def baixas_retrofit_plan(
    seller_slug: str,
    data_de: str = Query(..., description="YYYY-MM-DD"),
    data_ate: str = Query(..., description="YYYY-MM-DD"),
    limit: int | None = Query(None, description="Limita nº de baixas analisadas (piloto=1)"),
):
    """Plano de correção das baixas por-promessa (leitura pura — não escreve).
    PATCH=re-datar pela liberação real; DELETE=baixa sem liberação; MANUAL=exceção."""
    from app.services import baixas_retrofit
    return await baixas_retrofit.plan_retrofit(seller_slug, data_de, data_ate, limit=limit)


@router.post("/baixas-retrofit/{seller_slug}/apply", dependencies=[Depends(require_admin)])
async def baixas_retrofit_apply(
    seller_slug: str,
    data_de: str = Query(..., description="YYYY-MM-DD"),
    data_ate: str = Query(..., description="YYYY-MM-DD"),
    limit: int | None = Query(None, description="Piloto: aplicar só as N primeiras"),
):
    """Aplica o plano (enfileira PATCH/DELETE via ca_queue). Gated:
    seller precisa estar em baixa_extrato_write_sellers."""
    from app.services import baixas_retrofit
    plan = await baixas_retrofit.plan_retrofit(seller_slug, data_de, data_ate, limit=limit)
    result = await baixas_retrofit.apply_retrofit(seller_slug, plan)
    return {"plan_resumo": plan["resumo"], **result}


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
