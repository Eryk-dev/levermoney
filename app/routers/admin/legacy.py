"""
Legacy daily export endpoints: trigger, status.
"""
from fastapi import APIRouter, Depends, Query

from app.services.legacy_daily_export import (
    get_legacy_daily_status,
    run_legacy_daily_for_all,
    run_legacy_daily_for_seller,
)
from ._deps import require_admin

router = APIRouter()


# ── Legacy Daily Export ──────────────────────────────────────

@router.post("/legacy/daily/trigger", dependencies=[Depends(require_admin)])
async def trigger_legacy_daily(
    seller_slug: str | None = Query(None, description="If provided, run only for this seller"),
    target_day: str | None = Query(None, description="YYYY-MM-DD (default: yesterday BRT)"),
    upload: bool = Query(True, description="Upload generated ZIP to configured endpoint"),
):
    if seller_slug:
        result = await run_legacy_daily_for_seller(seller_slug, target_day=target_day, upload=upload)
        return {"mode": "single", "result": result}

    results = await run_legacy_daily_for_all(target_day=target_day, upload=upload)
    return {
        "mode": "all",
        "count": len(results),
        "ok": sum(1 for r in results if r.get("ok")),
        "failed": sum(1 for r in results if not r.get("ok")),
        "results": results,
    }


@router.get("/legacy/daily/status", dependencies=[Depends(require_admin)])
async def legacy_daily_status(
    seller_slug: str | None = Query(None, description="Filter by seller_slug"),
):
    return get_legacy_daily_status(seller_slug=seller_slug)
