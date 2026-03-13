"""
Expenses closing status endpoint.
"""
import logging
from collections import defaultdict

from fastapi import APIRouter, Depends, Query

from app.db.supabase import get_db
from app.models.sellers import get_seller_config
from app.routers.admin import require_admin
from ._deps import (
    MANUAL_EXPORTED_STATUSES,
    _signed_amount, _group_rows_by_day, _batch_tables_available,
    logger,
)

router = APIRouter()


@router.get("/{seller_slug}/closing", dependencies=[Depends(require_admin)])
async def closing_status(
    seller_slug: str,
    date_from: str | None = Query(None, description="YYYY-MM-DD"),
    date_to: str | None = Query(None, description="YYYY-MM-DD"),
    include_payment_ids: bool = Query(False, description="Include full payment_id lists"),
):
    """Daily closing status by company/day based on expense import status."""
    from app.services.event_ledger import get_expense_list

    db = get_db()
    seller = get_seller_config(db, seller_slug)
    if not seller:
        return {"error": f"Seller {seller_slug} not found"}

    rows = await get_expense_list(
        seller_slug=seller_slug,
        date_from=date_from,
        date_to=date_to,
        limit=1_000_000,
    )

    by_day = _group_rows_by_day(rows)
    company = seller.get("dashboard_empresa") or seller_slug

    imported_by_day: dict[str, set[int]] = defaultdict(set)
    import_source = "status_fallback"
    if _batch_tables_available(db):
        import_source = "batch_tables"
        try:
            bq = db.table("expense_batches").select("batch_id").eq(
                "seller_slug", seller_slug
            ).eq("status", "imported")
            if date_from:
                bq = bq.gte("imported_at", f"{date_from}T00:00:00.000-03:00")
            if date_to:
                bq = bq.lte("imported_at", f"{date_to}T23:59:59.999-03:00")

            batch_ids = [r["batch_id"] for r in (bq.execute().data or []) if r.get("batch_id")]
            for i in range(0, len(batch_ids), 100):
                chunk = batch_ids[i:i + 100]
                if not chunk:
                    continue
                iq = db.table("expense_batch_items").select("expense_date,payment_id").eq(
                    "seller_slug", seller_slug
                ).in_("batch_id", chunk)
                if date_from:
                    iq = iq.gte("expense_date", date_from)
                if date_to:
                    iq = iq.lte("expense_date", date_to)

                for item in iq.execute().data or []:
                    pid = item.get("payment_id")
                    day_key = item.get("expense_date")
                    if pid is None or not day_key:
                        continue
                    try:
                        imported_by_day[day_key].add(int(pid))
                    except (TypeError, ValueError):
                        continue
        except Exception as e:
            logger.warning(f"closing_status: failed to load imported batches for {seller_slug}: {e}")
            import_source = "status_fallback"

    days = []
    all_closed = True

    for day, day_rows in by_day.items():
        total_ids = {int(r["payment_id"]) for r in day_rows if r.get("payment_id") is not None}
        exported_ids = {
            int(r["payment_id"]) for r in day_rows
            if r.get("payment_id") is not None and r.get("status") in MANUAL_EXPORTED_STATUSES
        }
        imported_ids = set(imported_by_day.get(day, set()))
        if import_source == "status_fallback":
            imported_ids = set(exported_ids)
        missing_export_ids = sorted(total_ids - exported_ids)
        missing_import_ids = sorted(total_ids - imported_ids)

        total_signed = round(sum(_signed_amount(r) for r in day_rows), 2)
        exported_signed = round(sum(_signed_amount(r) for r in day_rows if r.get("status") in MANUAL_EXPORTED_STATUSES), 2)
        imported_signed = round(
            sum(_signed_amount(r) for r in day_rows if r.get("payment_id") is not None and int(r["payment_id"]) in imported_ids), 2
        )

        day_closed = len(missing_import_ids) == 0
        if not day_closed:
            all_closed = False

        day_item = {
            "date": day,
            "company": company,
            "rows_total": len(day_rows),
            "rows_exported": sum(1 for r in day_rows if r.get("status") in MANUAL_EXPORTED_STATUSES),
            "rows_imported": sum(
                1 for r in day_rows
                if r.get("payment_id") is not None and int(r["payment_id"]) in imported_ids
            ),
            "rows_not_exported": sum(1 for r in day_rows if r.get("status") not in MANUAL_EXPORTED_STATUSES),
            "rows_not_imported": sum(
                1 for r in day_rows
                if r.get("payment_id") is None or int(r["payment_id"]) not in imported_ids
            ),
            "amount_total_signed": total_signed,
            "amount_exported_signed": exported_signed,
            "amount_imported_signed": imported_signed,
            "amount_diff_export_signed": round(total_signed - exported_signed, 2),
            "amount_diff_import_signed": round(total_signed - imported_signed, 2),
            "payment_ids_total": len(total_ids),
            "payment_ids_exported": len(exported_ids),
            "payment_ids_imported": len(imported_ids),
            "payment_ids_missing_export": len(missing_export_ids),
            "payment_ids_missing_import": len(missing_import_ids),
            "payment_ids_missing_export_sample": missing_export_ids[:200],
            "payment_ids_missing_import_sample": missing_import_ids[:200],
            "closed": day_closed,
        }
        if include_payment_ids:
            day_item["payment_ids_total_list"] = sorted(total_ids)
            day_item["payment_ids_exported_list"] = sorted(exported_ids)
            day_item["payment_ids_imported_list"] = sorted(imported_ids)
            day_item["payment_ids_missing_export_list"] = missing_export_ids
            day_item["payment_ids_missing_import_list"] = missing_import_ids
        days.append(day_item)

    return {
        "seller": seller_slug,
        "company": company,
        "date_from": date_from,
        "date_to": date_to,
        "import_source": import_source,
        "days": days,
        "days_total": len(days),
        "days_closed": sum(1 for d in days if d["closed"]),
        "days_open": sum(1 for d in days if not d["closed"]),
        "all_closed": all_closed,
    }
