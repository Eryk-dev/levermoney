"""
Expenses CRUD endpoints: list, review/patch, pending-summary, and stats.
"""
import logging
from datetime import datetime

from fastapi import APIRouter, Depends, Query, HTTPException

from app.config import settings
from app.db.supabase import get_db
from app.routers.admin import require_admin
from ._deps import ExpenseReviewUpdate, _to_brt_iso_date

logger = logging.getLogger(__name__)

router = APIRouter()


# ── List expenses ──────────────────────────────────────────────

@router.get("/{seller_slug}", dependencies=[Depends(require_admin)])
async def list_expenses(
    seller_slug: str,
    status: str | None = Query(None, description="Filter by status"),
    expense_type: str | None = Query(None, description="Filter by expense_type"),
    direction: str | None = Query(None, description="Filter by expense_direction"),
    date_from: str | None = Query(None, description="Filter date_created >= YYYY-MM-DD"),
    date_to: str | None = Query(None, description="Filter date_created <= YYYY-MM-DD"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    """List mp_expenses for a seller with optional filters."""
    if settings.expenses_source == "ledger":
        from app.services.event_ledger import get_expense_list
        rows = await get_expense_list(
            seller_slug=seller_slug,
            status=status,
            expense_type=expense_type,
            direction=direction,
            date_from=date_from,
            date_to=date_to,
            limit=limit,
            offset=offset,
        )
        return {"seller": seller_slug, "count": len(rows), "offset": offset, "data": rows}

    db = get_db()
    q = db.table("mp_expenses").select(
        "id, payment_id, expense_type, expense_direction, ca_category, "
        "auto_categorized, amount, description, business_branch, operation_type, "
        "payment_method, external_reference, febraban_code, date_created, "
        "date_approved, beneficiary_name, notes, status, exported_at, created_at"
    ).eq("seller_slug", seller_slug).order("date_created", desc=True)

    if status:
        q = q.eq("status", status)
    if expense_type:
        q = q.eq("expense_type", expense_type)
    if direction:
        q = q.eq("expense_direction", direction)
    if date_from:
        q = q.gte("date_created", f"{date_from}T00:00:00.000-03:00")
    if date_to:
        q = q.lte("date_created", f"{date_to}T23:59:59.999-03:00")

    q = q.range(offset, offset + limit - 1)
    result = q.execute()

    return {
        "seller": seller_slug,
        "count": len(result.data or []),
        "offset": offset,
        "data": result.data or [],
    }


# ── Review / patch ─────────────────────────────────────────────

@router.patch("/review/{seller_slug}/{expense_id}", dependencies=[Depends(require_admin)])
async def review_expense(
    seller_slug: str,
    expense_id: int,
    req: ExpenseReviewUpdate,
):
    """Manually classify an expense and mark it as manually_categorized."""
    if settings.expenses_source == "ledger":
        from app.services.event_ledger import record_expense_event

        db = get_db()
        ref_id = str(expense_id)
        events = db.table("payment_events").select(
            "event_type, competencia_date, metadata"
        ).eq("seller_slug", seller_slug).eq(
            "reference_id", ref_id
        ).in_("event_type", [
            "expense_captured", "expense_exported", "expense_reviewed"
        ]).execute()

        event_types = {e["event_type"] for e in (events.data or [])}
        if "expense_captured" not in event_types:
            raise HTTPException(status_code=404, detail="Expense not found")
        if "expense_exported" in event_types:
            raise HTTPException(status_code=409, detail="Expense already exported")

        update_data = req.model_dump(exclude_none=True)
        if not update_data:
            raise HTTPException(status_code=400, detail="No fields to update")

        captured = next(
            (e for e in events.data if e["event_type"] == "expense_captured"), {}
        )
        competencia = captured.get("competencia_date", "")
        meta = captured.get("metadata") or {}
        expense_type_val = meta.get("expense_type", "unknown")

        await record_expense_event(
            seller_slug=seller_slug,
            payment_id=ref_id,
            event_type="expense_reviewed",
            signed_amount=0,
            competencia_date=competencia,
            expense_type=expense_type_val,
            metadata=update_data,
        )

        return {"ok": True, "status": "reviewed", **update_data}

    db = get_db()
    existing = db.table("mp_expenses").select("*").eq(
        "seller_slug", seller_slug
    ).eq("id", expense_id).execute()

    if not existing.data:
        raise HTTPException(status_code=404, detail="Expense not found")

    row = existing.data[0]
    if row.get("status") == "exported":
        raise HTTPException(status_code=409, detail="Expense already exported")

    update_data = req.model_dump(exclude_none=True)
    if not update_data:
        raise HTTPException(status_code=400, detail="No fields to update")

    update_data["status"] = "manually_categorized"
    update_data["auto_categorized"] = False
    update_data["updated_at"] = datetime.now().isoformat()

    result = db.table("mp_expenses").update(update_data).eq("id", expense_id).execute()
    return result.data[0] if result.data else {"ok": True}


# ── Pending review summary ──────────────────────────────────────

@router.get("/{seller_slug}/pending-summary", dependencies=[Depends(require_admin)])
async def pending_review_summary(
    seller_slug: str,
    date_from: str | None = Query(None, description="YYYY-MM-DD"),
    date_to: str | None = Query(None, description="YYYY-MM-DD"),
):
    """Summary of pending_review rows grouped by day."""
    if settings.expenses_source == "ledger":
        from app.services.event_ledger import get_expense_list
        rows = await get_expense_list(
            seller_slug=seller_slug,
            status="pending_review",
            date_from=date_from,
            date_to=date_to,
            limit=100_000,
            offset=0,
        )
    else:
        db = get_db()
        q = db.table("mp_expenses").select(
            "id, payment_id, amount, date_created, date_approved"
        ).eq("seller_slug", seller_slug).eq("status", "pending_review")

        if date_from:
            q = q.gte("date_created", f"{date_from}T00:00:00.000-03:00")
        if date_to:
            q = q.lte("date_created", f"{date_to}T23:59:59.999-03:00")

        rows = q.order("date_created", desc=False).execute().data or []

    by_day: dict[str, dict] = {}
    for row in rows:
        day = _to_brt_iso_date(row.get("date_approved") or row.get("date_created"))
        if day not in by_day:
            by_day[day] = {"count": 0, "amount_total": 0.0, "payment_ids": []}
        by_day[day]["count"] += 1
        by_day[day]["amount_total"] += float(row.get("amount") or 0)
        if len(by_day[day]["payment_ids"]) < 20 and row.get("payment_id"):
            by_day[day]["payment_ids"].append(row["payment_id"])

    return {
        "seller": seller_slug,
        "total_pending": len(rows),
        "by_day": [
            {
                "date": day,
                "count": info["count"],
                "amount_total": round(info["amount_total"], 2),
                "payment_ids_sample": info["payment_ids"],
            }
            for day, info in sorted(by_day.items(), key=lambda item: item[0])
        ],
    }


# ── Stats ──────────────────────────────────────────────────────

@router.get("/{seller_slug}/stats", dependencies=[Depends(require_admin)])
async def expense_stats(
    seller_slug: str,
    date_from: str | None = Query(None),
    date_to: str | None = Query(None),
    status_filter: str | None = Query(None, description="Comma-separated statuses, e.g. 'pending_review,auto_categorized'"),
):
    """Counters by expense_type, expense_direction, and status."""
    if settings.expenses_source == "ledger":
        from app.services.event_ledger import get_expense_stats as ledger_stats
        statuses = [s.strip() for s in status_filter.split(",") if s.strip()] if status_filter else None
        return await ledger_stats(
            seller_slug=seller_slug,
            date_from=date_from,
            date_to=date_to,
            status_filter=statuses,
        )

    db = get_db()
    q = db.table("mp_expenses").select("expense_type, expense_direction, status, amount").eq(
        "seller_slug", seller_slug
    )
    if status_filter:
        statuses = [s.strip() for s in status_filter.split(",") if s.strip()]
        q = q.in_("status", statuses)
    if date_from:
        q = q.gte("date_created", f"{date_from}T00:00:00.000-03:00")
    if date_to:
        q = q.lte("date_created", f"{date_to}T23:59:59.999-03:00")

    result = q.execute()
    rows = result.data or []

    by_type = {}
    by_direction = {}
    by_status = {}
    total_amount = 0.0

    for r in rows:
        t = r.get("expense_type", "unknown")
        d = r.get("expense_direction", "unknown")
        s = r.get("status", "unknown")
        amt = float(r.get("amount") or 0)

        by_type[t] = by_type.get(t, 0) + 1
        by_direction[d] = by_direction.get(d, 0) + 1
        by_status[s] = by_status.get(s, 0) + 1
        total_amount += amt

    return {
        "seller": seller_slug,
        "total": len(rows),
        "total_amount": round(total_amount, 2),
        "by_type": by_type,
        "by_direction": by_direction,
        "by_status": by_status,
        "pending_review_count": by_status.get("pending_review", 0),
        "auto_categorized_count": by_status.get("auto_categorized", 0),
    }
