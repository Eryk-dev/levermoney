"""
Queue monitoring endpoints for ca_jobs.
"""
import logging
from datetime import datetime, timezone

from fastapi import APIRouter

from app.db.supabase import get_db

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/queue", tags=["queue"])


@router.get("/status")
async def queue_status():
    """Count jobs by status."""
    db = get_db()
    result = db.rpc("get_ca_jobs_status_counts", {}).execute()
    if result.data:
        return {"counts": result.data}

    # Fallback: query each status individually
    counts = {}
    for status in ("pending", "processing", "completed", "failed", "dead"):
        r = db.table("ca_jobs").select("id", count="exact").eq("status", status).execute()
        counts[status] = r.count or 0
    return {"counts": counts}


@router.get("/dead")
async def list_dead_letters():
    """List dead-letter jobs for investigation."""
    db = get_db()
    result = db.table("ca_jobs").select("*").eq(
        "status", "dead"
    ).order("created_at", desc=True).limit(50).execute()
    return {"total": len(result.data), "jobs": result.data}


@router.post("/retry/{job_id}")
async def retry_job(job_id: str):
    """Manually retry a dead job."""
    db = get_db()
    now = datetime.now(timezone.utc).isoformat()

    result = db.table("ca_jobs").update({
        "status": "pending",
        "attempts": 0,
        "last_error": None,
        "next_retry_at": None,
        "updated_at": now,
    }).eq("id", job_id).eq("status", "dead").execute()

    if result.data:
        return {"ok": True, "job_id": job_id}
    return {"ok": False, "error": "Job not found or not in dead status"}


@router.post("/retry-all-dead")
async def retry_all_dead():
    """Reset all dead jobs back to pending."""
    db = get_db()
    now = datetime.now(timezone.utc).isoformat()

    result = db.table("ca_jobs").update({
        "status": "pending",
        "attempts": 0,
        "last_error": None,
        "next_retry_at": None,
        "updated_at": now,
    }).eq("status", "dead").execute()

    count = len(result.data) if result.data else 0
    return {"ok": True, "retried": count}
