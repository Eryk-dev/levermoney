"""
Admin API - password-protected endpoints for seller management, goals, sync.
Authentication via X-Admin-Token header verified against bcrypt hash in admin_config table.
"""
import asyncio
import logging
import secrets
from datetime import datetime, timezone
from typing import Any

import bcrypt
from fastapi import APIRouter, Depends, HTTPException, Header, Query
from pydantic import BaseModel

from app.config import settings
from app.db.supabase import get_db
from app.services.financial_closing import (
    compute_seller_financial_closing,
    get_last_financial_closing,
    run_financial_closing_for_all,
)
from app.services.legacy_daily_export import (
    get_legacy_daily_status,
    run_legacy_daily_for_all,
    run_legacy_daily_for_seller,
)
from app.services.release_report_validator import (
    get_last_validation_result,
    validate_release_fees_all_sellers,
    validate_release_fees_for_seller,
)
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
from app.services.onboarding_backfill import (
    get_backfill_status,
    retry_backfill,
    run_onboarding_backfill,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/admin", tags=["admin"])

# In-memory session tokens (simple approach, survives within process lifetime)
_sessions: dict[str, datetime] = {}


def _get_password_hash() -> str | None:
    db = get_db()
    result = db.table("admin_config").select("password_hash").eq("id", 1).execute()
    if result.data:
        return result.data[0]["password_hash"]
    return None


def _verify_password(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))


async def require_admin(x_admin_token: str = Header(...)):
    """Dependency: verify admin session token."""
    if x_admin_token not in _sessions:
        raise HTTPException(status_code=401, detail="Invalid or expired admin token")
    # Check expiry (24h sessions)
    created = _sessions[x_admin_token]
    if (datetime.now(timezone.utc) - created).total_seconds() > 86400:
        del _sessions[x_admin_token]
        raise HTTPException(status_code=401, detail="Session expired")
    return True


# ── Auth ──────────────────────────────────────────────────────

class LoginRequest(BaseModel):
    password: str


@router.post("/login")
async def login(req: LoginRequest):
    """Authenticate with admin password. Returns session token."""
    hashed = _get_password_hash()
    if not hashed:
        # First-time setup: hash and store the provided password
        new_hash = bcrypt.hashpw(req.password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
        db = get_db()
        db.table("admin_config").upsert({"id": 1, "password_hash": new_hash}).execute()
        hashed = new_hash

    if not _verify_password(req.password, hashed):
        raise HTTPException(status_code=401, detail="Invalid password")

    token = secrets.token_urlsafe(32)
    _sessions[token] = datetime.now(timezone.utc)
    return {"token": token}


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

    Does NOT delete the database row (FK constraints on payments, mp_expenses, etc.).
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
    Share the connect URL with the seller — they'll be redirected to ML OAuth.
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


# ── Revenue Lines ─────────────────────────────────────────────

@router.get("/revenue-lines", dependencies=[Depends(require_admin)])
async def list_revenue_lines():
    db = get_db()
    result = db.table("revenue_lines").select("*").order("created_at").execute()
    return result.data or []


class RevenueLineCreate(BaseModel):
    empresa: str
    grupo: str = "OUTROS"
    segmento: str = "OUTROS"
    source: str = "manual"


@router.post("/revenue-lines", dependencies=[Depends(require_admin)])
async def create_revenue_line(req: RevenueLineCreate):
    db = get_db()
    result = db.table("revenue_lines").insert(req.model_dump()).execute()
    return result.data[0] if result.data else {}


class RevenueLineUpdate(BaseModel):
    grupo: str | None = None
    segmento: str | None = None
    source: str | None = None
    active: bool | None = None


@router.patch("/revenue-lines/{empresa}", dependencies=[Depends(require_admin)])
async def update_revenue_line(empresa: str, req: RevenueLineUpdate):
    db = get_db()
    update_data = req.model_dump(exclude_none=True)
    if not update_data:
        raise HTTPException(status_code=400, detail="No fields to update")
    result = db.table("revenue_lines").update(update_data).eq("empresa", empresa).execute()
    return result.data[0] if result.data else {}


@router.delete("/revenue-lines/{empresa}", dependencies=[Depends(require_admin)])
async def delete_revenue_line(empresa: str):
    db = get_db()
    result = db.table("revenue_lines").update({"active": False}).eq("empresa", empresa).execute()
    return result.data[0] if result.data else {}


# ── Goals ─────────────────────────────────────────────────────

@router.get("/goals", dependencies=[Depends(require_admin)])
async def list_goals(year: int = 2026):
    db = get_db()
    result = db.table("goals").select("*").eq("year", year).execute()
    return result.data or []


class GoalEntry(BaseModel):
    empresa: str
    grupo: str
    year: int = 2026
    month: int
    valor: float


class GoalsBulk(BaseModel):
    goals: list[GoalEntry]


@router.post("/goals/bulk", dependencies=[Depends(require_admin)])
async def upsert_goals_bulk(req: GoalsBulk):
    db = get_db()
    rows = [g.model_dump() for g in req.goals]
    db.table("goals").upsert(rows, on_conflict="empresa,year,month").execute()
    return {"status": "ok", "count": len(rows)}


# ── Sync ──────────────────────────────────────────────────────

# syncer reference set by main.py
_syncer: Any = None


def set_syncer(syncer):
    global _syncer
    _syncer = syncer


@router.post("/sync/trigger", dependencies=[Depends(require_admin)])
async def trigger_sync():
    if not _syncer:
        raise HTTPException(status_code=503, detail="Syncer not initialized")
    results = await _syncer.sync_all()
    return {"last_sync": _syncer.last_sync, "results": results}


@router.get("/sync/status", dependencies=[Depends(require_admin)])
async def sync_status():
    if not _syncer:
        return {"last_sync": None, "results": []}
    return {"last_sync": _syncer.last_sync, "results": _syncer.last_results}


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


# ── Conta Azul Resources ─────────────────────────────────────

@router.get("/ca/contas-financeiras", dependencies=[Depends(require_admin)])
async def list_ca_accounts():
    from app.services.ca_api import listar_contas_financeiras
    try:
        raw = await listar_contas_financeiras()
        logger.info(f"CA contas-financeiras: {len(raw)} items")
        return [{"id": acc["id"], "nome": acc.get("nome", ""), "tipo": acc.get("tipo", "")} for acc in raw]
    except Exception as e:
        logger.error(f"CA contas-financeiras error: {e}")
        raise HTTPException(status_code=502, detail=str(e))


@router.get("/ca/centros-custo", dependencies=[Depends(require_admin)])
async def list_ca_cost_centers():
    from app.services.ca_api import listar_centros_custo
    try:
        raw = await listar_centros_custo()
        logger.info(f"CA centros-custo: {len(raw)} items")
        return [{"id": cc["id"], "descricao": cc.get("nome", "")} for cc in raw]
    except Exception as e:
        logger.error(f"CA centros-custo error: {e}")
        raise HTTPException(status_code=502, detail=str(e))


@router.post("/ca/categories/sync", dependencies=[Depends(require_admin)])
async def trigger_ca_categories_sync():
    """Manually trigger a sync of CA income/expense categories to the local JSON file."""
    from app.services.ca_categories_sync import sync_ca_categories
    try:
        result = await sync_ca_categories()
        return result
    except Exception as e:
        logger.error("CA categories sync error: %s", e, exc_info=True)
        raise HTTPException(status_code=502, detail=f"CA categories sync failed: {e}")


@router.get("/ca/categories/status", dependencies=[Depends(require_admin)])
async def ca_categories_sync_status():
    """Return the status of the last CA categories sync."""
    from app.services.ca_categories_sync import get_last_sync_result
    return get_last_sync_result()


@router.get("/ca/categories", dependencies=[Depends(require_admin)])
async def list_ca_categories():
    """List all CA categories from the local file. Auto-fetches from CA API if file is missing."""
    from app.services.ca_categories_sync import load_categories, sync_ca_categories
    cats = load_categories()
    if not cats:
        await sync_ca_categories()
        cats = load_categories()
    return cats


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


class UpgradeToCaRequest(BaseModel):
    ca_conta_bancaria: str
    ca_centro_custo_variavel: str
    ca_start_date: str  # YYYY-MM-DD, must be 1st of month


@router.post("/sellers/{slug}/upgrade-to-ca", dependencies=[Depends(require_admin)])
async def upgrade_seller_to_ca(slug: str, req: UpgradeToCaRequest):
    """Upgrade an active dashboard_only seller to dashboard_ca integration.

    Validates that the seller is active and currently in dashboard_only mode.
    Sets CA config fields and launches the onboarding backfill in the background.

    Returns {"status": "ok", "backfill_triggered": true}.
    """
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

    update_data = {
        "integration_mode": "dashboard_ca",
        "ca_conta_bancaria": req.ca_conta_bancaria,
        "ca_centro_custo_variavel": req.ca_centro_custo_variavel,
        "ca_start_date": req.ca_start_date,
        "ca_backfill_status": "pending",
    }
    db.table("sellers").update(update_data).eq("slug", slug).execute()
    logger.info("upgrade_seller_to_ca %s: ca_start_date=%s", slug, req.ca_start_date)

    asyncio.create_task(run_onboarding_backfill(slug))
    logger.info("upgrade_seller_to_ca %s: onboarding backfill task launched", slug)

    return {"status": "ok", "backfill_triggered": True}


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

    The backfill is idempotent — it resumes from where it left off by skipping
    payments already present in the payments and mp_expenses tables.
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
