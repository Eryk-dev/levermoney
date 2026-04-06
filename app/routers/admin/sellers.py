"""
Seller CRUD endpoints: list, pending, approve, reject, patch, delete,
disconnect, reconnect, activate, upgrade, backfill.
"""
import asyncio
import logging

from typing import List

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from app.config import settings
from app.db.supabase import get_db
from app.models.sellers import get_seller_config
from app.services.onboarding_backfill import (
    get_backfill_status,
    retry_backfill,
    run_onboarding_backfill,
)
from .extrato import _decode_csv_bytes, _MAX_FILE_SIZE, process_extrato_files
from ._deps import require_admin

logger = logging.getLogger(__name__)
router = APIRouter()


# ── Sellers ───────────────────────────────────────────────────

@router.get("/sellers", dependencies=[Depends(require_admin)])
async def list_sellers():
    db = get_db()
    result = db.table("sellers").select("*").order("created_at").execute()
    return result.data or []


@router.get("/sellers/pending", dependencies=[Depends(require_admin)])
async def list_pending_sellers():
    db = get_db()
    result = db.table("sellers").select("*").eq("onboarding_status", "pending_approval").execute()
    return result.data or []


class ApproveRequest(BaseModel):
    dashboard_empresa: str
    dashboard_grupo: str = "OUTROS"
    dashboard_segmento: str = "OUTROS"
    ca_conta_bancaria: str | None = None
    ca_centro_custo_variavel: str | None = None
    ca_contato_ml: str | None = None
    ml_app_id: str | None = None
    ml_secret_key: str | None = None


@router.post("/sellers/{seller_id}/approve", dependencies=[Depends(require_admin)])
async def approve_seller(seller_id: str, req: ApproveRequest):
    from app.services.onboarding import approve_seller as do_approve
    seller = await do_approve(seller_id, req.model_dump(exclude_none=True))
    return seller


@router.post("/sellers/{seller_id}/reject", dependencies=[Depends(require_admin)])
async def reject_seller(seller_id: str):
    from app.services.onboarding import reject_seller as do_reject
    return await do_reject(seller_id)


class SellerUpdate(BaseModel):
    name: str | None = None
    dashboard_empresa: str | None = None
    dashboard_grupo: str | None = None
    dashboard_segmento: str | None = None
    ca_conta_bancaria: str | None = None
    ca_centro_custo_variavel: str | None = None
    ca_contato_ml: str | None = None
    ml_app_id: str | None = None
    ml_secret_key: str | None = None


@router.patch("/sellers/{seller_id}", dependencies=[Depends(require_admin)])
async def update_seller(seller_id: str, req: SellerUpdate):
    db = get_db()
    update_data = req.model_dump(exclude_none=True)
    if not update_data:
        raise HTTPException(status_code=400, detail="No fields to update")
    result = db.table("sellers").update(update_data).eq("id", seller_id).execute()
    return result.data[0] if result.data else {}


@router.delete("/sellers/{slug}", dependencies=[Depends(require_admin)])
async def delete_seller(slug: str):
    """Soft-delete a seller: deactivates, clears ML tokens, sets status to suspended.

    Does NOT delete the database row (FK constraints on payment_events, etc.).
    The seller can re-authenticate later via the install link or reconnect link.
    """
    db = get_db()
    result = db.table("sellers").select("slug, onboarding_status").eq("slug", slug).execute()
    if not result.data:
        raise HTTPException(status_code=404, detail=f"Seller '{slug}' not found")

    db.table("sellers").update({
        "active": False,
        "onboarding_status": "suspended",
        "ml_access_token": None,
        "ml_refresh_token": None,
        "ml_token_expires_at": None,
    }).eq("slug", slug).execute()

    logger.info("Seller soft-deleted: %s", slug)
    return {
        "status": "ok",
        "message": f"Seller '{slug}' suspended and tokens cleared. "
                   f"Can re-authenticate via /auth/ml/connect?seller={slug} or /auth/ml/install",
    }


@router.post("/sellers/{slug}/disconnect", dependencies=[Depends(require_admin)])
async def disconnect_seller(slug: str):
    """Disconnect a seller's ML integration: clears ML tokens but keeps seller config.

    Use this when a seller revoked permissions in ML and needs to re-authenticate.
    The seller stays active but ML API calls will fail until re-authenticated.
    """
    db = get_db()
    result = db.table("sellers").select("slug").eq("slug", slug).execute()
    if not result.data:
        raise HTTPException(status_code=404, detail=f"Seller '{slug}' not found")

    db.table("sellers").update({
        "ml_access_token": None,
        "ml_refresh_token": None,
        "ml_token_expires_at": None,
    }).eq("slug", slug).execute()

    logger.info("Seller ML tokens cleared (disconnect): %s", slug)
    return {
        "status": "ok",
        "reconnect_url": f"{settings.base_url}/auth/ml/connect?seller={slug}",
        "message": f"ML tokens cleared for '{slug}'. Share the reconnect_url with the seller to re-authenticate.",
    }


@router.get("/sellers/{slug}/reconnect-link", dependencies=[Depends(require_admin)])
async def get_reconnect_link(slug: str):
    """Get a reconnect link for a seller to re-authenticate with ML.

    Returns both the direct connect URL (for known sellers) and the install URL.
    Share the connect URL with the seller -- they'll be redirected to ML OAuth.
    """
    db = get_db()
    result = db.table("sellers").select("slug, active, onboarding_status, ml_access_token").eq("slug", slug).execute()
    if not result.data:
        raise HTTPException(status_code=404, detail=f"Seller '{slug}' not found")

    seller = result.data[0]
    has_valid_tokens = bool(seller.get("ml_access_token"))

    return {
        "slug": slug,
        "active": seller.get("active"),
        "onboarding_status": seller.get("onboarding_status"),
        "has_valid_tokens": has_valid_tokens,
        "reconnect_url": f"{settings.base_url}/auth/ml/connect?seller={slug}",
        "install_url": f"{settings.base_url}/auth/ml/install",
    }


# ── Onboarding V2 ────────────────────────────────────────────


class ActivateSellerRequest(BaseModel):
    integration_mode: str  # "dashboard_only" | "dashboard_ca"
    name: str | None = None
    dashboard_empresa: str | None = None
    dashboard_grupo: str = "OUTROS"
    dashboard_segmento: str = "OUTROS"
    ca_conta_bancaria: str | None = None
    ca_centro_custo_variavel: str | None = None
    ca_start_date: str | None = None  # YYYY-MM-DD, must be 1st of month
    skip_extrato: bool = False


@router.post("/sellers/{slug}/activate", dependencies=[Depends(require_admin)])
async def activate_seller_v2(slug: str, req: ActivateSellerRequest):
    """Activate a seller (pending_approval or any status) with V2 integration mode.

    For dashboard_ca: requires ca_conta_bancaria, ca_centro_custo_variavel,
    ca_start_date (must be the 1st of a month). Triggers onboarding backfill
    as a background task.

    Returns {"status": "ok", "backfill_triggered": true/false}.
    """
    if req.integration_mode not in ("dashboard_only", "dashboard_ca"):
        raise HTTPException(
            status_code=400,
            detail="integration_mode must be 'dashboard_only' or 'dashboard_ca'",
        )

    if req.integration_mode == "dashboard_ca":
        missing = [
            f for f in ("ca_conta_bancaria", "ca_centro_custo_variavel", "ca_start_date")
            if not getattr(req, f)
        ]
        if missing:
            raise HTTPException(
                status_code=400,
                detail=f"dashboard_ca requires: {', '.join(missing)}",
            )
        # Validate ca_start_date is the 1st of a month
        try:
            from datetime import date as _date
            _parsed = _date.fromisoformat(req.ca_start_date)
            if _parsed.day != 1:
                raise HTTPException(
                    status_code=400,
                    detail=f"ca_start_date must be the 1st of a month, got {req.ca_start_date}",
                )
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail=f"ca_start_date is not a valid date: {req.ca_start_date}",
            )

    db = get_db()

    # Load seller to verify it exists
    result = db.table("sellers").select("*").eq("slug", slug).limit(1).execute()
    if not result.data:
        raise HTTPException(status_code=404, detail=f"Seller '{slug}' not found")

    seller = result.data[0]

    # Build update payload
    update_data: dict = {
        "integration_mode": req.integration_mode,
        "onboarding_status": "active",
        "active": True,
    }
    if req.name:
        update_data["name"] = req.name
    if req.dashboard_empresa:
        update_data["dashboard_empresa"] = req.dashboard_empresa
    update_data["dashboard_grupo"] = req.dashboard_grupo
    update_data["dashboard_segmento"] = req.dashboard_segmento
    if req.integration_mode == "dashboard_ca":
        update_data["ca_conta_bancaria"] = req.ca_conta_bancaria
        update_data["ca_centro_custo_variavel"] = req.ca_centro_custo_variavel
        update_data["ca_start_date"] = req.ca_start_date
        update_data["ca_backfill_status"] = "pending"
        update_data["extrato_missing"] = req.skip_extrato

    db.table("sellers").update(update_data).eq("slug", slug).execute()
    logger.info("activate_seller_v2 %s: mode=%s", slug, req.integration_mode)

    # Create revenue_line and goals (only if not already present)
    empresa = req.dashboard_empresa or seller.get("dashboard_empresa")
    if empresa:
        from datetime import datetime as _dt
        grupo = req.dashboard_grupo
        segmento = req.dashboard_segmento

        db.table("revenue_lines").upsert(
            {
                "empresa": empresa,
                "grupo": grupo,
                "segmento": segmento,
                "seller_id": seller.get("id"),
                "source": seller.get("source", "ml"),
                "active": True,
            },
            on_conflict="empresa",
        ).execute()

        year = _dt.now().year
        goals = [
            {"empresa": empresa, "grupo": grupo, "year": year, "month": m, "valor": 0}
            for m in range(1, 13)
        ]
        db.table("goals").upsert(
            goals, on_conflict="empresa,year,month", ignore_duplicates=True
        ).execute()
        logger.info("activate_seller_v2 %s: revenue_line + goals ensured for empresa=%s", slug, empresa)

    # Auto-configure release report (best-effort)
    try:
        from app.services.ml_api import configure_release_report
        await configure_release_report(slug)
        logger.info("activate_seller_v2 %s: release report configured", slug)
    except Exception as exc:
        logger.warning("activate_seller_v2 %s: failed to configure release report: %s", slug, exc)

    backfill_triggered = False
    if req.integration_mode == "dashboard_ca":
        asyncio.create_task(run_onboarding_backfill(slug))
        backfill_triggered = True
        logger.info("activate_seller_v2 %s: onboarding backfill task launched", slug)

    return {"status": "ok", "backfill_triggered": backfill_triggered}


@router.post("/sellers/{slug}/upgrade-to-ca", dependencies=[Depends(require_admin)])
async def upgrade_seller_to_ca(
    slug: str,
    ca_conta_bancaria: str = Form(...),
    ca_centro_custo_variavel: str = Form(...),
    ca_start_date: str = Form(...),
    files: List[UploadFile] = File(...),
):
    """Upgrade an active dashboard_only seller to dashboard_ca integration.

    Accepts multipart/form-data with CA config fields and extrato CSV files.
    Extrato is mandatory: CSVs must cover the full period from ca_start_date
    to yesterday. Each file is processed separately per month.

    Flow: validate config → validate CSV coverage → update seller to
    dashboard_ca → ingest extratos → launch backfill in background.

    Returns {"status": "ok", "backfill_triggered": true, "extrato": {...}}.
    """
    # Validate ca_start_date is the 1st of a month
    try:
        from datetime import date as _date
        _parsed = _date.fromisoformat(ca_start_date)
        if _parsed.day != 1:
            raise HTTPException(
                status_code=400,
                detail=f"ca_start_date must be the 1st of a month, got {ca_start_date}",
            )
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"ca_start_date is not a valid date: {ca_start_date}",
        )

    db = get_db()
    result = db.table("sellers").select("*").eq("slug", slug).limit(1).execute()
    if not result.data:
        raise HTTPException(status_code=404, detail=f"Seller '{slug}' not found")

    seller = result.data[0]

    if not seller.get("active"):
        raise HTTPException(status_code=400, detail=f"Seller '{slug}' is not active")

    current_mode = seller.get("integration_mode", "dashboard_only")
    if current_mode == "dashboard_ca":
        raise HTTPException(
            status_code=400,
            detail=f"Seller '{slug}' is already in dashboard_ca mode",
        )

    # --- Read and decode CSV files ---
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

    # --- Validate coverage + ingest ---
    # process_extrato_files raises HTTPException 422 on coverage failure.
    # Update seller to dashboard_ca FIRST so that extrato ingestion works
    # (it needs integration_mode=dashboard_ca for event ledger context).
    update_data = {
        "integration_mode": "dashboard_ca",
        "ca_conta_bancaria": ca_conta_bancaria,
        "ca_centro_custo_variavel": ca_centro_custo_variavel,
        "ca_start_date": ca_start_date,
        "ca_backfill_status": "pending",
        "extrato_missing": False,
    }
    db.table("sellers").update(update_data).eq("slug", slug).execute()
    logger.info("upgrade_seller_to_ca %s: ca_start_date=%s", slug, ca_start_date)

    try:
        # Re-read seller after update (process_extrato_files needs current state)
        seller = get_seller_config(db, slug)
        extrato_result = await process_extrato_files(
            db, slug, seller, files_data, ca_start_date,
        )
    except HTTPException:
        # Coverage validation failed — rollback seller to dashboard_only
        db.table("sellers").update({
            "integration_mode": "dashboard_only",
            "ca_conta_bancaria": None,
            "ca_centro_custo_variavel": None,
            "ca_start_date": None,
            "ca_backfill_status": None,
            "extrato_missing": False,
        }).eq("slug", slug).execute()
        logger.warning(
            "upgrade_seller_to_ca %s: rolled back to dashboard_only (extrato validation failed)",
            slug,
        )
        raise
    except Exception as exc:
        # Unexpected error — also rollback
        db.table("sellers").update({
            "integration_mode": "dashboard_only",
            "ca_conta_bancaria": None,
            "ca_centro_custo_variavel": None,
            "ca_start_date": None,
            "ca_backfill_status": None,
            "extrato_missing": False,
        }).eq("slug", slug).execute()
        logger.error(
            "upgrade_seller_to_ca %s: rolled back to dashboard_only (unexpected error): %s",
            slug, exc, exc_info=True,
        )
        raise HTTPException(status_code=500, detail=f"Extrato processing failed: {exc}")

    # --- Launch backfill ---
    asyncio.create_task(run_onboarding_backfill(slug))
    logger.info("upgrade_seller_to_ca %s: onboarding backfill task launched", slug)

    return {
        "status": "ok",
        "backfill_triggered": True,
        "extrato": extrato_result,
    }


@router.post("/sellers/{slug}/disconnect-ca", dependencies=[Depends(require_admin)])
async def disconnect_seller_ca(slug: str):
    """Disconnect a seller from Conta Azul, reverting to dashboard_only mode.

    Clears all CA config fields but preserves historical data (payment_events,
    extrato_uploads, etc.).
    """
    db = get_db()
    result = db.table("sellers").select("slug, integration_mode, active").eq("slug", slug).limit(1).execute()
    if not result.data:
        raise HTTPException(status_code=404, detail=f"Seller '{slug}' not found")

    seller = result.data[0]
    if seller.get("integration_mode") != "dashboard_ca":
        raise HTTPException(
            status_code=400,
            detail=f"Seller '{slug}' is not in dashboard_ca mode",
        )

    db.table("sellers").update({
        "integration_mode": "dashboard_only",
        "ca_conta_bancaria": None,
        "ca_centro_custo_variavel": None,
        "ca_start_date": None,
        "ca_backfill_status": None,
        "ca_backfill_started_at": None,
        "ca_backfill_completed_at": None,
        "ca_backfill_progress": None,
        "extrato_missing": False,
        "extrato_uploaded_at": None,
    }).eq("slug", slug).execute()
    logger.info("disconnect_seller_ca %s: reverted to dashboard_only", slug)

    return {"status": "ok", "slug": slug, "integration_mode": "dashboard_only"}


@router.get("/sellers/{slug}/backfill-status", dependencies=[Depends(require_admin)])
async def seller_backfill_status(slug: str):
    """Return the current onboarding backfill status and progress for a seller."""
    try:
        return get_backfill_status(slug)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        logger.error("backfill_status error for %s: %s", slug, exc, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Backfill status error: {exc}")


@router.post("/sellers/{slug}/backfill-retry", dependencies=[Depends(require_admin)])
async def seller_backfill_retry(slug: str):
    """Re-trigger a failed onboarding backfill for a seller.

    The backfill is idempotent -- it resumes from where it left off by skipping
    payments already present in the payment_events table.
    """
    try:
        await retry_backfill(slug)
        return {"status": "ok"}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.error("backfill_retry error for %s: %s", slug, exc, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Backfill retry error: {exc}")


@router.get("/onboarding/install-link", dependencies=[Depends(require_admin)])
async def onboarding_install_link():
    """Return the ML OAuth install link to share with prospective sellers."""
    return {"url": f"{settings.base_url}/auth/ml/install"}
