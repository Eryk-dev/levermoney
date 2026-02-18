"""
Dashboard API - public endpoints for the React dashboard.
Read access to revenue_lines, goals, faturamento.
"""
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.db.supabase import get_db

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("/revenue-lines")
async def get_revenue_lines():
    """List active revenue lines."""
    db = get_db()
    result = db.table("revenue_lines").select("*").eq("active", True).execute()
    return result.data or []


@router.get("/goals")
async def get_goals(year: int = 2026):
    """Get goals for a year."""
    db = get_db()
    result = db.table("goals").select("*").eq("year", year).execute()
    return result.data or []


class FaturamentoEntry(BaseModel):
    empresa: str
    date: str  # YYYY-MM-DD
    valor: float


@router.post("/faturamento/entry")
async def upsert_faturamento_entry(entry: FaturamentoEntry):
    """Upsert a manual faturamento entry."""
    db = get_db()
    try:
        db.table("faturamento").upsert(
            {
                "empresa": entry.empresa,
                "data": entry.date,
                "valor": entry.valor,
                "source": "manual",
                "updated_at": datetime.now(timezone.utc).isoformat(),
            },
            on_conflict="empresa,data",
        ).execute()
        return {"status": "ok"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class FaturamentoDelete(BaseModel):
    empresa: str
    date: str  # YYYY-MM-DD


@router.post("/faturamento/delete")
async def delete_faturamento_entry(entry: FaturamentoDelete):
    """Delete a faturamento entry."""
    db = get_db()
    try:
        db.table("faturamento").delete().eq("empresa", entry.empresa).eq("data", entry.date).execute()
        return {"status": "ok"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
