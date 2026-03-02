"""
Extrato endpoints: coverage, ingest, ingestion-status.
"""
import logging

from fastapi import APIRouter, Depends, HTTPException, Query

from app.services.extrato_coverage_checker import (
    check_extrato_coverage,
    check_extrato_coverage_all_sellers,
    get_last_coverage_result,
)
from app.services.extrato_ingester import (
    get_last_ingestion_result,
    ingest_extrato_all_sellers,
    ingest_extrato_for_seller,
)
from ._deps import require_admin

logger = logging.getLogger(__name__)
router = APIRouter()


# ── Extrato Coverage ─────────────────────────────────────────

@router.get("/extrato/coverage/{seller_slug}", dependencies=[Depends(require_admin)])
async def extrato_coverage(
    seller_slug: str,
    date_from: str = Query(..., description="YYYY-MM-DD"),
    date_to: str = Query(..., description="YYYY-MM-DD"),
):
    """Check release report coverage for a specific seller."""
    try:
        result = await check_extrato_coverage(seller_slug, date_from, date_to)
        return result
    except Exception as e:
        logger.error("Extrato coverage error for %s: %s", seller_slug, e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Coverage check failed: {e}")


@router.post("/extrato/coverage-all", dependencies=[Depends(require_admin)])
async def extrato_coverage_all(
    lookback_days: int = Query(3, description="Number of days to look back"),
):
    """Check release report coverage for all active sellers."""
    try:
        results = await check_extrato_coverage_all_sellers(lookback_days=lookback_days)
        return {
            "count": len(results),
            "results": results,
        }
    except Exception as e:
        logger.error("Extrato coverage-all error: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Coverage check failed: {e}")


@router.get("/extrato/coverage-status", dependencies=[Depends(require_admin)])
async def extrato_coverage_status():
    """Return the result of the last coverage check run."""
    return get_last_coverage_result()


# ── Extrato Ingester ─────────────────────────────────────────


@router.post("/extrato/ingest/{seller_slug}", dependencies=[Depends(require_admin)])
async def trigger_extrato_ingest(
    seller_slug: str,
    begin_date: str = Query(..., description="YYYY-MM-DD"),
    end_date: str = Query(..., description="YYYY-MM-DD"),
):
    """Manually trigger account_statement ingestion for a specific seller.

    Ingests extrato lines not already covered by the payments or mp_expenses
    tables and inserts them as mp_expenses rows.
    """
    try:
        result = await ingest_extrato_for_seller(seller_slug, begin_date, end_date)
        return result
    except Exception as exc:
        logger.error(
            "extrato ingest error for %s: %s", seller_slug, exc, exc_info=True
        )
        raise HTTPException(status_code=500, detail=f"Extrato ingest failed: {exc}")


@router.post("/extrato/ingest-all", dependencies=[Depends(require_admin)])
async def trigger_extrato_ingest_all(
    lookback_days: int = Query(3, description="Number of days to look back from yesterday"),
):
    """Trigger account_statement ingestion for all active sellers.

    Runs the same pipeline used by the nightly scheduler.
    """
    try:
        results = await ingest_extrato_all_sellers(lookback_days=lookback_days)
        return {
            "count": len(results),
            "total_ingested": sum(r.get("newly_ingested", 0) for r in results),
            "total_errors": sum(r.get("errors", 0) for r in results),
            "results": results,
        }
    except Exception as exc:
        logger.error("extrato ingest-all error: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Extrato ingest-all failed: {exc}")


@router.get("/extrato/ingestion-status", dependencies=[Depends(require_admin)])
async def extrato_ingestion_status():
    """Return the result of the last extrato ingestion run."""
    return get_last_ingestion_result()
