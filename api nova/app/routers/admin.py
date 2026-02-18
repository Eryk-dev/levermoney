"""
Admin API - password-protected endpoints for seller management, goals, sync.
Authentication via X-Admin-Token header verified against bcrypt hash in admin_config table.
"""
import hashlib
import logging
import secrets
from datetime import datetime, timezone
from typing import Any

import bcrypt
from fastapi import APIRouter, Depends, HTTPException, Header, Query
from pydantic import BaseModel

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
    return {"results": results}


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
