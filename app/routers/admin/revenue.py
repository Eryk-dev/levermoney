"""
Revenue lines + goals + sync endpoints.
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.db.supabase import get_db
from ._deps import get_syncer, require_admin

router = APIRouter()


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

@router.post("/sync/trigger", dependencies=[Depends(require_admin)])
async def trigger_sync():
    _syncer = get_syncer()
    if not _syncer:
        raise HTTPException(status_code=503, detail="Syncer not initialized")
    results = await _syncer.sync_all()
    return {"last_sync": _syncer.last_sync, "results": results}


@router.get("/sync/status", dependencies=[Depends(require_admin)])
async def sync_status():
    _syncer = get_syncer()
    if not _syncer:
        return {"last_sync": None, "results": []}
    return {"last_sync": _syncer.last_sync, "results": _syncer.last_results}
