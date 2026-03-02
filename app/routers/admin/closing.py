"""
Financial closing endpoints: trigger, status, seller detail.
"""
from fastapi import APIRouter, Depends, Query

from app.services.financial_closing import (
    compute_seller_financial_closing,
    get_last_financial_closing,
    run_financial_closing_for_all,
)
from ._deps import require_admin

router = APIRouter()


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
