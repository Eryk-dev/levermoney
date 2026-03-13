"""
Extrato endpoints: coverage, ingest, ingestion-status, CSV upload.
"""
import json
import logging
import re

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile

from app.db.supabase import get_db
from app.models.sellers import get_seller_config
from app.services.extrato_coverage_checker import (
    check_extrato_coverage,
    check_extrato_coverage_all_sellers,
    get_last_coverage_result,
)
from app.services.extrato_ingester import (
    get_last_ingestion_result,
    ingest_extrato_all_sellers,
    ingest_extrato_for_seller,
    ingest_extrato_from_csv,
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

    Ingests extrato lines not already covered by payments or expense events
    and records them as expense_captured events in the event ledger.
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


# ── Extrato Upload ──────────────────────────────────────────

_MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
_MAX_FILE_SIZE = 5 * 1024 * 1024  # 5 MB


@router.post("/extrato/upload", dependencies=[Depends(require_admin)])
async def upload_extrato(
    file: UploadFile = File(...),
    seller_slug: str = Form(...),
    month: str = Form(...),
):
    """Upload an account_statement CSV and ingest gap lines as expense events.

    Re-upload of the same (seller_slug, month) is safe: the extrato_uploads
    record is upserted and event ledger dedup prevents duplicate entries.
    """
    # --- Validate month format ---
    if not _MONTH_RE.match(month):
        raise HTTPException(
            status_code=422,
            detail=f"Invalid month format. Expected YYYY-MM, got '{month}'",
        )

    # --- Validate seller exists ---
    db = get_db()
    seller = get_seller_config(db, seller_slug)
    if not seller:
        raise HTTPException(status_code=404, detail=f"Seller '{seller_slug}' not found")

    # --- Read and validate file size ---
    raw_bytes = await file.read()
    size_mb = len(raw_bytes) / (1024 * 1024)
    if len(raw_bytes) > _MAX_FILE_SIZE:
        raise HTTPException(
            status_code=413,
            detail=f"File too large: {size_mb:.1f}MB exceeds 5MB limit",
        )

    # --- Decode (utf-8-sig then latin-1 fallback) ---
    try:
        csv_text = raw_bytes.decode("utf-8-sig")
    except UnicodeDecodeError:
        csv_text = raw_bytes.decode("latin-1")

    # --- Validate CSV format ---
    if "INITIAL_BALANCE" not in csv_text.upper():
        raise HTTPException(
            status_code=422,
            detail=(
                "Invalid CSV: INITIAL_BALANCE header not found. "
                "Make sure you downloaded the account statement (extrato) "
                "from Mercado Pago."
            ),
        )

    # --- Insert processing record (upsert by seller_slug + month) ---
    upload_row = {
        "seller_slug": seller_slug,
        "month": month,
        "filename": file.filename,
        "status": "processing",
        "error_message": None,
        "lines_total": None,
        "lines_ingested": None,
        "lines_skipped": None,
        "lines_already_covered": None,
        "initial_balance": None,
        "final_balance": None,
        "summary": None,
    }
    upsert_resp = (
        db.table("extrato_uploads")
        .upsert(upload_row, on_conflict="seller_slug,month")
        .execute()
    )
    upload_id = upsert_resp.data[0]["id"] if upsert_resp.data else None

    # --- Run ingestion ---
    try:
        result = await ingest_extrato_from_csv(seller_slug, csv_text, month)
    except Exception as exc:
        # Mark upload as failed
        if upload_id:
            db.table("extrato_uploads").update({
                "status": "failed",
                "error_message": str(exc)[:500],
            }).eq("id", upload_id).execute()
        logger.error(
            "extrato upload failed for %s month=%s: %s",
            seller_slug, month, exc, exc_info=True,
        )
        raise HTTPException(status_code=500, detail=f"Ingestion failed: {exc}")

    # --- Update upload record with results ---
    summary = result.get("summary") or {}
    update_data = {
        "status": "completed",
        "lines_total": result.get("total_lines", 0),
        "lines_ingested": result.get("newly_ingested", 0),
        "lines_skipped": result.get("skipped_internal", 0),
        "lines_already_covered": result.get("already_covered", 0),
        "initial_balance": summary.get("initial_balance"),
        "final_balance": summary.get("final_balance"),
        "summary": json.dumps(result.get("by_type", {})),
    }
    if upload_id:
        db.table("extrato_uploads").update(update_data).eq("id", upload_id).execute()

    return {
        "upload_id": upload_id,
        "seller_slug": seller_slug,
        "month": month,
        "filename": file.filename,
        "status": "completed",
        "lines_total": result.get("total_lines", 0),
        "lines_ingested": result.get("newly_ingested", 0),
        "lines_skipped": result.get("skipped_internal", 0),
        "lines_already_covered": result.get("already_covered", 0),
        "amount_updated": result.get("amount_updated", 0),
        "initial_balance": summary.get("initial_balance"),
        "final_balance": summary.get("final_balance"),
        "gaps_found": result.get("by_type", {}),
    }


# ── Extrato Upload History ─────────────────────────────────────


@router.get("/extrato/uploads/{seller_slug}", dependencies=[Depends(require_admin)])
async def list_extrato_uploads(
    seller_slug: str,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    """List extrato upload history for a seller, ordered by most recent first."""
    db = get_db()
    result = (
        db.table("extrato_uploads")
        .select(
            "id, filename, month, status, lines_total, lines_ingested, "
            "lines_skipped, lines_already_covered, initial_balance, "
            "final_balance, error_message, uploaded_at"
        )
        .eq("seller_slug", seller_slug)
        .order("uploaded_at", desc=True)
        .range(offset, offset + limit - 1)
        .execute()
    )
    return {
        "seller": seller_slug,
        "count": len(result.data or []),
        "data": result.data or [],
    }
