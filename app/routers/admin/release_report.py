"""
Release report endpoints: sync, validate, configure.
"""
import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from app.services.release_report_validator import (
    get_last_validation_result,
    validate_release_fees_all_sellers,
    validate_release_fees_for_seller,
)
from ._deps import require_admin

logger = logging.getLogger(__name__)
router = APIRouter()


# ── Release Report Sync ─────────────────────────────────────


class ReleaseReportSyncRequest(BaseModel):
    seller: str
    begin_date: str
    end_date: str


@router.post("/release-report/sync", dependencies=[Depends(require_admin)])
async def sync_release_report(req: ReleaseReportSyncRequest):
    """Sync release report for a seller: fetch CSV, parse, and insert new mp_expenses."""
    from app.services.release_report_sync import sync_release_report as do_sync
    try:
        result = await do_sync(req.seller, req.begin_date, req.end_date)
        return result
    except Exception as e:
        logger.error("Release report sync error for %s: %s", req.seller, e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Release report sync failed: {e}")


# ── Release Report Fee Validation ───────────────────────────

@router.post("/release-report/validate/{seller_slug}", dependencies=[Depends(require_admin)])
async def trigger_release_report_validation(
    seller_slug: str,
    begin_date: str = Query(..., description="YYYY-MM-DD"),
    end_date: str = Query(..., description="YYYY-MM-DD"),
):
    """Validate processor fees against release report for a specific seller."""
    try:
        result = await validate_release_fees_for_seller(seller_slug, begin_date, end_date)
        return result
    except Exception as e:
        logger.error("Release report validation error for %s: %s", seller_slug, e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Validation failed: {e}")


@router.post("/release-report/validate-all", dependencies=[Depends(require_admin)])
async def trigger_release_report_validation_all(
    lookback_days: int = Query(3, description="Number of days to look back"),
):
    """Validate processor fees against release report for all active sellers."""
    try:
        results = await validate_release_fees_all_sellers(lookback_days=lookback_days)
        return {
            "count": len(results),
            "total_adjustments": sum(r.get("adjustments_created", 0) for r in results),
            "results": results,
        }
    except Exception as e:
        logger.error("Release report validation-all error: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Validation failed: {e}")


@router.get("/release-report/validation-status", dependencies=[Depends(require_admin)])
async def release_report_validation_status():
    """Return the result of the last fee validation run."""
    return get_last_validation_result()


@router.post("/release-report/configure/{seller_slug}", dependencies=[Depends(require_admin)])
async def configure_release_report(seller_slug: str):
    """Configure release report columns with fee breakdown for a seller."""
    from app.services.ml_api import configure_release_report as do_configure, get_release_report_config
    try:
        result = await do_configure(seller_slug)
        return {"status": "configured", "config": result}
    except Exception as e:
        logger.error("Release report configure error for %s: %s", seller_slug, e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Configure failed: {e}")


@router.get("/release-report/config/{seller_slug}", dependencies=[Depends(require_admin)])
async def get_release_report_config_endpoint(seller_slug: str):
    """Get current release report configuration for a seller."""
    from app.services.ml_api import get_release_report_config
    try:
        config = await get_release_report_config(seller_slug)
        return config
    except Exception as e:
        logger.error("Release report get config error for %s: %s", seller_slug, e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Get config failed: {e}")
