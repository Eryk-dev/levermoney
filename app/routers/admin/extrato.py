"""
Extrato endpoints: coverage, ingest, ingestion-status, CSV upload (single + multi).
"""
import asyncio
import json
import logging
import re
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import List

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile

from app.db.supabase import get_db
from app.models.sellers import get_seller_config
from app.services.extrato_coverage_checker import (
    check_extrato_coverage,
    check_extrato_coverage_all_sellers,
    get_last_coverage_result,
)
from app.services.extrato_ingester import (
    _months_range,
    _parse_account_statement,
    get_last_ingestion_result,
    ingest_extrato_all_sellers,
    ingest_extrato_for_seller,
    ingest_extrato_from_csv,
    validate_extrato_coverage,
)
from app.services.gdrive_client import upload_extrato_csv
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


# ── Sellers Extrato Status ───────────────────────────────────


@router.get("/extrato/sellers-status", dependencies=[Depends(require_admin)])
async def extrato_sellers_status():
    """Return extrato coverage status for all active dashboard_ca sellers."""
    db = get_db()

    # Fetch all active dashboard_ca sellers
    sellers_resp = (
        db.table("sellers")
        .select(
            "slug, name, dashboard_empresa, ca_start_date, "
            "extrato_missing, extrato_uploaded_at, integration_mode, active"
        )
        .eq("integration_mode", "dashboard_ca")
        .eq("active", True)
        .execute()
    )
    sellers = sellers_resp.data or []

    if not sellers:
        return []

    # Fetch all completed uploads
    uploads_resp = (
        db.table("extrato_uploads")
        .select("seller_slug, month")
        .eq("status", "completed")
        .execute()
    )
    # Group months by seller_slug
    uploads_by_seller: dict[str, set[str]] = defaultdict(set)
    for row in uploads_resp.data or []:
        uploads_by_seller[row["seller_slug"]].add(row["month"])

    # Current month (BRT)
    now_brt = datetime.now(timezone(timedelta(hours=-3)))
    current_month_end = date(now_brt.year, now_brt.month, 1)

    result = []
    for s in sellers:
        ca_start = s.get("ca_start_date")
        if not ca_start:
            result.append({
                "slug": s["slug"],
                "name": s.get("name"),
                "dashboard_empresa": s.get("dashboard_empresa"),
                "ca_start_date": None,
                "extrato_missing": s.get("extrato_missing") or False,
                "extrato_uploaded_at": s.get("extrato_uploaded_at"),
                "months_needed": [],
                "months_uploaded": [],
                "months_missing": [],
                "coverage_status": "missing",
            })
            continue

        # Normalize ca_start_date
        ca_start_str = str(ca_start)[:10]
        start_date = date.fromisoformat(ca_start_str)
        months_needed = _months_range(start_date, current_month_end)

        slug = s["slug"]
        months_uploaded = sorted(uploads_by_seller.get(slug, set()))
        uploaded_set = set(months_uploaded)
        months_missing = [m for m in months_needed if m not in uploaded_set]

        if not months_missing:
            coverage_status = "complete"
        elif len(months_uploaded) > 0:
            coverage_status = "partial"
        else:
            coverage_status = "missing"

        result.append({
            "slug": slug,
            "name": s.get("name"),
            "dashboard_empresa": s.get("dashboard_empresa"),
            "ca_start_date": ca_start_str,
            "extrato_missing": s.get("extrato_missing") or False,
            "extrato_uploaded_at": s.get("extrato_uploaded_at"),
            "months_needed": months_needed,
            "months_uploaded": months_uploaded,
            "months_missing": months_missing,
            "coverage_status": coverage_status,
        })

    return result


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


# ── Multi-file Extrato Upload ─────────────────────────────────

_MAX_FILE_SIZE = 5 * 1024 * 1024  # 5 MB


def _decode_csv_bytes(raw_bytes: bytes) -> str:
    """Decode CSV bytes with utf-8-sig then latin-1 fallback."""
    try:
        return raw_bytes.decode("utf-8-sig")
    except UnicodeDecodeError:
        return raw_bytes.decode("latin-1")


async def process_extrato_files(
    db,
    slug: str,
    seller: dict,
    files_data: list[tuple[str, bytes, str]],
    ca_start_date: str,
) -> dict:
    """Shared logic for processing extrato CSV uploads.

    Validates coverage, ingests gap lines per month, updates extrato_uploads,
    updates seller flags, and starts GDrive backup in background.

    Args:
        db: Supabase client.
        slug: Seller slug.
        seller: Seller dict from DB.
        files_data: List of (csv_text, raw_bytes, filename) tuples.
        ca_start_date: YYYY-MM-DD string (must be 1st of month).

    Returns:
        Dict with total_files, total_lines, total_ingested, total_errors,
        months_processed, gdrive_status, results (per-month details).

    Raises:
        HTTPException 422 on coverage validation failure.
    """
    csv_texts = [fd[0] for fd in files_data]

    # --- Validate coverage ---
    coverage = validate_extrato_coverage(csv_texts, ca_start_date)
    if not coverage["valid"]:
        raise HTTPException(
            status_code=422,
            detail={
                "message": f"Coverage validation failed: {coverage['error']}",
                "covered_months": coverage["covered_months"],
                "missing_months": coverage["missing_months"],
                "gaps": coverage["gaps"],
                "min_date": coverage["min_date"],
                "max_date": coverage["max_date"],
            },
        )

    # --- Detect months per CSV ---
    month_to_csv: dict[str, tuple[str, bytes, str]] = {}

    for csv_text, raw_bytes, fname in files_data:
        _summary, transactions = _parse_account_statement(csv_text)
        months_in_csv: set[str] = set()
        for tx in transactions:
            months_in_csv.add(tx["date"][:7])

        for month in sorted(months_in_csv):
            if month not in month_to_csv:
                month_to_csv[month] = (csv_text, raw_bytes, fname)

    # Only process months within the needed range
    needed_months = set(coverage["covered_months"])
    months_to_process = sorted(m for m in month_to_csv if m in needed_months)

    # --- Ingest each month ---
    results_per_month: list[dict] = []
    total_ingested = 0
    total_errors = 0
    total_lines = 0

    for month in months_to_process:
        csv_text, raw_bytes, fname = month_to_csv[month]

        upload_row = {
            "seller_slug": slug,
            "month": month,
            "filename": fname,
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

        try:
            result = await ingest_extrato_from_csv(slug, csv_text, month)
        except Exception as exc:
            if upload_id:
                db.table("extrato_uploads").update({
                    "status": "failed",
                    "error_message": str(exc)[:500],
                }).eq("id", upload_id).execute()
            logger.error(
                "multi-upload ingest failed for %s month=%s: %s",
                slug, month, exc, exc_info=True,
            )
            results_per_month.append({
                "month": month,
                "filename": fname,
                "status": "failed",
                "error": str(exc)[:200],
            })
            total_errors += 1
            continue

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

        month_lines = result.get("total_lines", 0)
        month_ingested = result.get("newly_ingested", 0)
        total_lines += month_lines
        total_ingested += month_ingested

        results_per_month.append({
            "month": month,
            "filename": fname,
            "status": "completed",
            "lines_total": month_lines,
            "lines_ingested": month_ingested,
            "lines_skipped": result.get("skipped_internal", 0),
            "lines_already_covered": result.get("already_covered", 0),
        })

    # --- Update seller flags ---
    now_iso = datetime.now(timezone(timedelta(hours=-3))).isoformat()
    db.table("sellers").update({
        "extrato_missing": False,
        "extrato_uploaded_at": now_iso,
    }).eq("slug", slug).execute()

    # --- GDrive backup (background) ---
    gdrive_status = "skipped"
    for month in months_to_process:
        csv_text, raw_bytes, fname = month_to_csv[month]
        gdrive_filename = f"{month}.csv"

        async def _upload_to_gdrive(
            _slug: str = slug,
            _seller: dict = seller,
            _bytes: bytes = raw_bytes,
            _month: str = month,
            _fname: str = gdrive_filename,
        ) -> None:
            try:
                await asyncio.to_thread(
                    upload_extrato_csv, _slug, _seller, _bytes, _month, _fname,
                )
            except Exception as exc:
                logger.error(
                    "gdrive extrato upload failed for %s month=%s: %s",
                    _slug, _month, exc, exc_info=True,
                )

        asyncio.create_task(_upload_to_gdrive())
        gdrive_status = "queued"

    return {
        "total_files": len(files_data),
        "total_lines": total_lines,
        "total_ingested": total_ingested,
        "total_errors": total_errors,
        "months_processed": months_to_process,
        "gdrive_status": gdrive_status,
        "results": results_per_month,
    }


async def save_extrato_files(
    db,
    slug: str,
    seller: dict,
    files_data: list[tuple[str, bytes, str]],
    ca_start_date: str,
) -> dict:
    """Validate coverage and save extrato CSVs without ingesting.

    Used by upgrade-to-ca: CSVs are stored in extrato_uploads with
    status='pending_ingestion' and csv_content populated. The actual
    ingestion happens after the onboarding backfill completes (so that
    payment_events are populated and the ingester can correctly identify
    gaps instead of treating everything as missing).

    Raises:
        HTTPException 422 on coverage validation failure.
    """
    csv_texts = [fd[0] for fd in files_data]

    # --- Validate coverage ---
    coverage = validate_extrato_coverage(csv_texts, ca_start_date)
    if not coverage["valid"]:
        raise HTTPException(
            status_code=422,
            detail={
                "message": f"Coverage validation failed: {coverage['error']}",
                "covered_months": coverage["covered_months"],
                "missing_months": coverage["missing_months"],
                "gaps": coverage["gaps"],
                "min_date": coverage["min_date"],
                "max_date": coverage["max_date"],
            },
        )

    # --- Detect months per CSV ---
    month_to_csv: dict[str, tuple[str, bytes, str]] = {}

    for csv_text, raw_bytes, fname in files_data:
        _summary, transactions = _parse_account_statement(csv_text)
        months_in_csv: set[str] = set()
        for tx in transactions:
            months_in_csv.add(tx["date"][:7])

        for month in sorted(months_in_csv):
            if month not in month_to_csv:
                month_to_csv[month] = (csv_text, raw_bytes, fname)

    needed_months = set(coverage["covered_months"])
    months_to_process = sorted(m for m in month_to_csv if m in needed_months)

    # --- Save each month as pending_ingestion ---
    for month in months_to_process:
        csv_text, raw_bytes, fname = month_to_csv[month]

        upload_row = {
            "seller_slug": slug,
            "month": month,
            "filename": fname,
            "status": "pending_ingestion",
            "csv_content": csv_text,
            "error_message": None,
            "lines_total": None,
            "lines_ingested": None,
            "lines_skipped": None,
            "lines_already_covered": None,
            "initial_balance": None,
            "final_balance": None,
            "summary": None,
        }
        db.table("extrato_uploads").upsert(
            upload_row, on_conflict="seller_slug,month"
        ).execute()

    # --- Update seller flags ---
    now_iso = datetime.now(timezone(timedelta(hours=-3))).isoformat()
    db.table("sellers").update({
        "extrato_missing": False,
        "extrato_uploaded_at": now_iso,
    }).eq("slug", slug).execute()

    # --- GDrive backup (background) ---
    gdrive_status = "skipped"
    for month in months_to_process:
        csv_text, raw_bytes, fname = month_to_csv[month]
        gdrive_filename = f"{month}.csv"

        async def _upload_to_gdrive(
            _slug: str = slug,
            _seller: dict = seller,
            _bytes: bytes = raw_bytes,
            _month: str = month,
            _fname: str = gdrive_filename,
        ) -> None:
            try:
                await asyncio.to_thread(
                    upload_extrato_csv, _slug, _seller, _bytes, _month, _fname,
                )
            except Exception as exc:
                logger.error(
                    "gdrive extrato upload failed for %s month=%s: %s",
                    _slug, _month, exc, exc_info=True,
                )

        asyncio.create_task(_upload_to_gdrive())
        gdrive_status = "queued"

    logger.info(
        "save_extrato_files %s: saved %d months (pending_ingestion), gdrive=%s",
        slug, len(months_to_process), gdrive_status,
    )

    return {
        "total_files": len(files_data),
        "months_saved": months_to_process,
        "gdrive_status": gdrive_status,
    }


@router.post("/sellers/{slug}/extrato/upload", dependencies=[Depends(require_admin)])
async def upload_extrato_multi(
    slug: str,
    files: List[UploadFile] = File(...),
):
    """Upload multiple extrato CSVs for a seller with coverage validation.

    Auto-detects months from CSV content. Validates full coverage from
    ca_start_date to yesterday. Ingests gap lines and backs up to GDrive.
    """
    db = get_db()
    seller = get_seller_config(db, slug)
    if not seller:
        raise HTTPException(status_code=404, detail=f"Seller '{slug}' not found")

    if seller.get("integration_mode") != "dashboard_ca":
        raise HTTPException(
            status_code=422,
            detail="Seller integration_mode must be 'dashboard_ca' for extrato upload",
        )

    ca_start_date = seller.get("ca_start_date")
    if not ca_start_date:
        raise HTTPException(
            status_code=422,
            detail="Seller has no ca_start_date configured",
        )
    ca_start_date = str(ca_start_date)[:10]

    # --- Read and decode all files ---
    files_data: list[tuple[str, bytes, str]] = []
    for f in files:
        raw_bytes = await f.read()
        if len(raw_bytes) > _MAX_FILE_SIZE:
            size_mb = len(raw_bytes) / (1024 * 1024)
            raise HTTPException(
                status_code=413,
                detail=f"File '{f.filename}' too large: {size_mb:.1f}MB exceeds 5MB limit",
            )
        csv_text = _decode_csv_bytes(raw_bytes)
        files_data.append((csv_text, raw_bytes, f.filename or "unknown.csv"))

    result = await process_extrato_files(db, slug, seller, files_data, ca_start_date)
    return {"seller_slug": slug, **result}


# ── Extrato Upload (single file, legacy) ────────────────────

_MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


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
