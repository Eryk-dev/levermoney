"""
Queue monitoring endpoints for ca_jobs.
"""
import logging
from datetime import datetime, timezone

from fastapi import APIRouter

from app.db.supabase import get_db
from app.services.event_ledger import derive_payment_status, get_payment_statuses

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/queue", tags=["queue"])


def _extract_payment_id_from_group(group_id: str | None) -> int | None:
    """Parse payment_id from group_id format: {seller_slug}:{payment_id}."""
    if not group_id:
        return None
    parts = group_id.split(":")
    if len(parts) < 2:
        return None
    try:
        return int(parts[1])
    except (TypeError, ValueError):
        return None


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


@router.get("/reconciliation/{seller_slug}")
async def queue_reconciliation(
    seller_slug: str,
    date_from: str | None = None,
    date_to: str | None = None,
    sample_limit: int = 200,
):
    """Operational reconciliation view for automatic CA sync by payment_id."""
    db = get_db()
    sample_limit = max(1, min(sample_limit, 1000))

    # Derive payment statuses via event_ledger helper
    payment_statuses = await get_payment_statuses(seller_slug, date_from, date_to)

    status_counts: dict[str, int] = {}
    open_payment_ids = []
    error_payment_ids = []

    for pid, st in payment_statuses.items():
        if st == "error":
            error_payment_ids.append(pid)
        elif st in ("queued", "unknown"):
            open_payment_ids.append(pid)
        status_counts[st] = status_counts.get(st, 0) + 1

    # Load all jobs for seller (paginated)
    jobs = []
    page_start = 0
    page_limit = 1000
    while True:
        batch = db.table("ca_jobs").select("group_id, status").eq(
            "seller_slug", seller_slug
        ).range(page_start, page_start + page_limit - 1).execute().data or []
        jobs.extend(batch)
        if len(batch) < page_limit:
            break
        page_start += page_limit

    dead_job_payment_ids: set[int] = set()
    pending_job_payment_ids: set[int] = set()

    for job in jobs:
        pid = _extract_payment_id_from_group(job.get("group_id"))
        if pid is None:
            continue
        st = job.get("status")
        if st == "dead":
            dead_job_payment_ids.add(pid)
        elif st in {"pending", "failed", "processing"}:
            pending_job_payment_ids.add(pid)

    not_fully_reconciled = sorted(
        set(open_payment_ids) | set(error_payment_ids) | dead_job_payment_ids | pending_job_payment_ids
    )

    return {
        "seller": seller_slug,
        "date_from": date_from,
        "date_to": date_to,
        "payments_total": len(payment_statuses),
        "payments_by_status": status_counts,
        "payments_open_count": len(set(open_payment_ids)),
        "payments_open_sample": sorted(set(open_payment_ids))[:sample_limit],
        "payments_with_error_count": len(set(error_payment_ids)),
        "payments_with_error_sample": sorted(set(error_payment_ids))[:sample_limit],
        "dead_job_payment_ids_count": len(dead_job_payment_ids),
        "dead_job_payment_ids_sample": sorted(dead_job_payment_ids)[:sample_limit],
        "pending_job_payment_ids_count": len(pending_job_payment_ids),
        "pending_job_payment_ids_sample": sorted(pending_job_payment_ids)[:sample_limit],
        "not_fully_reconciled_count": len(not_fully_reconciled),
        "not_fully_reconciled_sample": not_fully_reconciled[:sample_limit],
    }
