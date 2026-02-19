"""
Financial closing service.

Combines:
- Automatic lane (order payments -> Conta Azul via ca_jobs/payments)
- Manual lane (non-order payments -> XLSX export/import via mp_expenses + expense_batches)
"""
import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from app.db.supabase import get_db
from app.models.sellers import get_all_active_sellers, get_seller_config

logger = logging.getLogger(__name__)

FINAL_PAYMENT_STATUSES = {"synced", "refunded", "skipped", "skipped_non_sale"}
MANUAL_EXPORTED_STATUSES = {"exported"}

_last_closing_result: dict = {
    "ran_at": None,
    "date_from": None,
    "date_to": None,
    "results": [],
}


def _extract_payment_id_from_group(group_id: str | None) -> int | None:
    if not group_id:
        return None
    parts = group_id.split(":")
    if len(parts) < 2:
        return None
    try:
        return int(parts[1])
    except (TypeError, ValueError):
        return None


def _to_brt_day(iso_str: str | None) -> str:
    if not iso_str:
        return "sem-data"
    try:
        dt = datetime.fromisoformat(iso_str)
        return dt.astimezone(timezone(timedelta(hours=-3))).strftime("%Y-%m-%d")
    except Exception:
        return iso_str[:10] if iso_str else "sem-data"


def _signed_amount(row: dict) -> float:
    amount = float(row.get("amount") or 0)
    direction = row.get("expense_direction", "expense")
    if direction == "income":
        return abs(amount)
    return -abs(amount)


def _batch_tables_available(db) -> bool:
    try:
        db.table("expense_batches").select("batch_id").limit(1).execute()
        db.table("expense_batch_items").select("batch_id").limit(1).execute()
        return True
    except Exception:
        return False


def _paginate(query_builder, page_limit: int = 1000) -> list[dict]:
    rows = []
    start = 0
    while True:
        batch = query_builder.range(start, start + page_limit - 1).execute().data or []
        rows.extend(batch)
        if len(batch) < page_limit:
            break
        start += page_limit
    return rows


def _compute_manual_lane(
    db,
    seller_slug: str,
    date_from: str | None,
    date_to: str | None,
) -> tuple[list[dict], set[int], str]:
    """Return (days, missing_import_ids, import_source)."""
    q = db.table("mp_expenses").select(
        "payment_id, amount, expense_direction, status, date_created, date_approved"
    ).eq("seller_slug", seller_slug)
    if date_from:
        q = q.gte("date_created", f"{date_from}T00:00:00.000-03:00")
    if date_to:
        q = q.lte("date_created", f"{date_to}T23:59:59.999-03:00")

    rows = q.order("date_created", desc=False).execute().data or []
    by_day: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        by_day[_to_brt_day(row.get("date_approved") or row.get("date_created"))].append(row)

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
            logger.warning("financial_closing manual lane: failed to load imported batches: %s", e)
            import_source = "status_fallback"

    days = []
    missing_import_ids: set[int] = set()
    for day, day_rows in sorted(by_day.items(), key=lambda x: x[0]):
        total_ids = {int(r["payment_id"]) for r in day_rows if r.get("payment_id") is not None}
        exported_ids = {
            int(r["payment_id"]) for r in day_rows
            if r.get("payment_id") is not None and r.get("status") in MANUAL_EXPORTED_STATUSES
        }
        imported_ids = set(imported_by_day.get(day, set()))
        if import_source == "status_fallback":
            imported_ids = set(exported_ids)

        miss_export = sorted(total_ids - exported_ids)
        miss_import = sorted(total_ids - imported_ids)
        missing_import_ids.update(miss_import)

        total_signed = round(sum(_signed_amount(r) for r in day_rows), 2)
        exported_signed = round(
            sum(_signed_amount(r) for r in day_rows if r.get("payment_id") is not None and int(r["payment_id"]) in exported_ids),
            2,
        )
        imported_signed = round(
            sum(_signed_amount(r) for r in day_rows if r.get("payment_id") is not None and int(r["payment_id"]) in imported_ids),
            2,
        )

        days.append({
            "date": day,
            "rows_total": len(day_rows),
            "rows_exported": sum(
                1 for r in day_rows
                if r.get("payment_id") is not None and int(r["payment_id"]) in exported_ids
            ),
            "rows_imported": sum(
                1 for r in day_rows
                if r.get("payment_id") is not None and int(r["payment_id"]) in imported_ids
            ),
            "amount_total_signed": total_signed,
            "amount_exported_signed": exported_signed,
            "amount_imported_signed": imported_signed,
            "payment_ids_total": len(total_ids),
            "payment_ids_exported": len(exported_ids),
            "payment_ids_imported": len(imported_ids),
            "payment_ids_missing_export": len(miss_export),
            "payment_ids_missing_import": len(miss_import),
            "payment_ids_missing_export_sample": miss_export[:200],
            "payment_ids_missing_import_sample": miss_import[:200],
            "closed": len(miss_import) == 0,
        })

    return days, missing_import_ids, import_source


def _compute_auto_lane(
    db,
    seller_slug: str,
    date_from: str | None,
    date_to: str | None,
) -> dict:
    q = db.table("payments").select(
        "ml_payment_id, status, error, updated_at"
    ).eq("seller_slug", seller_slug)
    if date_from:
        q = q.gte("updated_at", f"{date_from}T00:00:00.000-03:00")
    if date_to:
        q = q.lte("updated_at", f"{date_to}T23:59:59.999-03:00")
    payments = _paginate(q.order("updated_at", desc=False))

    status_counts: dict[str, int] = {}
    open_ids: set[int] = set()
    err_ids: set[int] = set()
    for p in payments:
        st = p.get("status") or "unknown"
        status_counts[st] = status_counts.get(st, 0) + 1
        pid = p.get("ml_payment_id")
        if pid is None:
            continue
        try:
            pid_int = int(pid)
        except (TypeError, ValueError):
            continue
        if st not in FINAL_PAYMENT_STATUSES:
            open_ids.add(pid_int)
        if p.get("error"):
            err_ids.add(pid_int)

    jq = db.table("ca_jobs").select("group_id,status,created_at").eq("seller_slug", seller_slug)
    if date_from:
        jq = jq.gte("created_at", f"{date_from}T00:00:00.000-03:00")
    if date_to:
        jq = jq.lte("created_at", f"{date_to}T23:59:59.999-03:00")
    jobs = _paginate(jq.order("created_at", desc=False))

    dead_ids: set[int] = set()
    pending_ids: set[int] = set()
    for j in jobs:
        pid = _extract_payment_id_from_group(j.get("group_id"))
        if pid is None:
            continue
        st = j.get("status")
        if st == "dead":
            dead_ids.add(pid)
        elif st in {"pending", "failed", "processing"}:
            pending_ids.add(pid)

    unresolved = sorted(open_ids | err_ids | dead_ids | pending_ids)
    return {
        "payments_total": len(payments),
        "payments_by_status": status_counts,
        "open_payment_ids_count": len(open_ids),
        "open_payment_ids_sample": sorted(open_ids)[:200],
        "error_payment_ids_count": len(err_ids),
        "error_payment_ids_sample": sorted(err_ids)[:200],
        "dead_job_payment_ids_count": len(dead_ids),
        "dead_job_payment_ids_sample": sorted(dead_ids)[:200],
        "pending_job_payment_ids_count": len(pending_ids),
        "pending_job_payment_ids_sample": sorted(pending_ids)[:200],
        "unresolved_payment_ids_count": len(unresolved),
        "unresolved_payment_ids_sample": unresolved[:200],
        "unresolved_payment_ids_set": set(unresolved),
    }


async def compute_seller_financial_closing(
    seller_slug: str,
    date_from: str | None = None,
    date_to: str | None = None,
) -> dict:
    db = get_db()
    seller = get_seller_config(db, seller_slug)
    if not seller:
        return {"seller": seller_slug, "error": "seller_not_found"}

    auto = _compute_auto_lane(db, seller_slug, date_from, date_to)
    manual_days, manual_missing_ids, import_source = _compute_manual_lane(
        db, seller_slug, date_from, date_to
    )
    unresolved_combined = sorted(auto["unresolved_payment_ids_set"] | manual_missing_ids)

    return {
        "seller": seller_slug,
        "company": seller.get("dashboard_empresa") or seller_slug,
        "date_from": date_from,
        "date_to": date_to,
        "auto": {
            k: v for k, v in auto.items() if k != "unresolved_payment_ids_set"
        },
        "manual": {
            "import_source": import_source,
            "days_total": len(manual_days),
            "days_closed": sum(1 for d in manual_days if d["closed"]),
            "days_open": sum(1 for d in manual_days if not d["closed"]),
            "days": manual_days,
            "missing_import_payment_ids_count": len(manual_missing_ids),
            "missing_import_payment_ids_sample": sorted(manual_missing_ids)[:200],
        },
        "unresolved_payment_ids_count": len(unresolved_combined),
        "unresolved_payment_ids_sample": unresolved_combined[:200],
        "closed": len(unresolved_combined) == 0,
    }


async def run_financial_closing_for_all(
    date_from: str | None = None,
    date_to: str | None = None,
) -> dict:
    """Run closing for all active sellers and keep last result in memory."""
    db = get_db()
    sellers = get_all_active_sellers(db)

    # Default window: yesterday in BRT.
    if not date_from or not date_to:
        brt_now = datetime.now(timezone(timedelta(hours=-3)))
        yday = (brt_now - timedelta(days=1)).strftime("%Y-%m-%d")
        date_from = date_from or yday
        date_to = date_to or yday

    results = []
    for s in sellers:
        slug = s["slug"]
        try:
            result = await compute_seller_financial_closing(slug, date_from, date_to)
            results.append(result)
        except Exception as e:
            logger.error("financial_closing error for %s: %s", slug, e, exc_info=True)
            results.append({"seller": slug, "error": str(e), "closed": False})

    # Attach coverage data if available from last nightly run
    coverage_data = None
    try:
        from app.services.extrato_coverage_checker import get_last_coverage_result
        coverage_data = get_last_coverage_result()
    except Exception:
        pass

    summary = {
        "ran_at": datetime.now(timezone.utc).isoformat(),
        "date_from": date_from,
        "date_to": date_to,
        "sellers_total": len(results),
        "sellers_closed": sum(1 for r in results if r.get("closed")),
        "sellers_open": sum(1 for r in results if not r.get("closed")),
        "results": results,
    }
    if coverage_data and coverage_data.get("ran_at"):
        summary["extrato_coverage"] = {
            "ran_at": coverage_data["ran_at"],
            "sellers_checked": len(coverage_data.get("results", [])),
            "sellers_100pct": sum(
                1 for r in coverage_data.get("results", [])
                if r.get("coverage_pct", 0) >= 100.0
            ),
        }

    _last_closing_result.update(summary)
    return summary


def get_last_financial_closing() -> dict:
    return _last_closing_result
