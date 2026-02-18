"""
Backfill: puxa payments ML retroativos e processa no CA.
GET /backfill/{seller_slug}?begin_date=2026-01-01&end_date=2026-02-11&dry_run=true
"""
import asyncio
import logging
from fastapi import APIRouter, Query

from app.db.supabase import get_db
from app.models.sellers import get_seller_config
from app.services import ml_api
from app.services.processor import process_payment_webhook

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/backfill")


@router.get("/{seller_slug}")
async def backfill_payments(
    seller_slug: str,
    begin_date: str = Query(..., description="Data início YYYY-MM-DD"),
    end_date: str = Query(..., description="Data fim YYYY-MM-DD"),
    dry_run: bool = Query(True, description="Se True, apenas lista sem processar"),
    max_process: int = Query(0, description="Máximo de payments a processar (0=todos)"),
    concurrency: int = Query(10, description="Payments processados em paralelo"),
):
    """
    Puxa payments do ML por período e processa no CA.
    Use dry_run=true primeiro para ver o que será processado.
    """
    concurrency = max(1, min(20, concurrency))

    db = get_db()
    seller = get_seller_config(db, seller_slug)
    if not seller:
        return {"error": f"Seller {seller_slug} not found"}

    # Format dates for MP API (timezone BRT -03:00)
    begin = f"{begin_date}T00:00:00.000-03:00"
    end_dt = f"{end_date}T23:59:59.999-03:00"

    # Paginate through all results
    all_payments = []
    offset = 0
    page_size = 50

    while True:
        result = await ml_api.search_payments(seller_slug, begin, end_dt, offset, page_size)
        payments = result.get("results", [])
        total = result.get("paging", {}).get("total", 0)

        all_payments.extend(payments)
        offset += len(payments)

        logger.info(f"Backfill fetch: got {len(all_payments)}/{total} payments")

        if offset >= total or not payments:
            break

        await asyncio.sleep(0.3)

    # Summary by status
    status_counts = {}
    for p in all_payments:
        s = p.get("status", "unknown")
        status_counts[s] = status_counts.get(s, 0) + 1

    total_amount = sum(p.get("transaction_amount", 0) for p in all_payments)

    # Check which are already processed in Supabase (terminal statuses)
    # Paginate to avoid Supabase's default 1000-row limit
    already_done = set()
    page_start = 0
    page_limit = 1000
    while True:
        done_result = db.table("payments").select("ml_payment_id").eq(
            "seller_slug", seller_slug
        ).in_(
            "status", ["synced", "queued", "refunded", "skipped", "skipped_non_sale"]
        ).range(page_start, page_start + page_limit - 1).execute()
        batch = done_result.data or []
        already_done.update(r["ml_payment_id"] for r in batch)
        if len(batch) < page_limit:
            break
        page_start += page_limit

    processable = [
        p for p in all_payments
        if p.get("status") in ("approved", "refunded", "in_mediation", "charged_back")
        and p["id"] not in already_done
        and (p.get("order") or {}).get("id")  # filter non-sale payments
        and p.get("description") != "marketplace_shipment"  # skip buyer-paid shipping
        and (p.get("collector") or {}).get("id") is None  # skip purchases (seller is buyer)
    ]

    if dry_run:
        return {
            "mode": "dry_run",
            "seller": seller_slug,
            "period": f"{begin_date} to {end_date}",
            "total_payments": len(all_payments),
            "total_amount": round(total_amount, 2),
            "by_status": status_counts,
            "already_done": len(already_done),
            "to_process": len(processable),
            "concurrency": concurrency,
            "sample": [
                {
                    "id": p["id"],
                    "status": p["status"],
                    "amount": p["transaction_amount"],
                    "date": (p.get("date_approved") or p.get("date_created", ""))[:19],
                    "order_id": p.get("order", {}).get("id") if p.get("order") else None,
                    "net": p.get("transaction_details", {}).get("net_received_amount"),
                }
                for p in processable[:20]
            ],
        }

    # === PROCESS MODE ===
    to_process = processable
    if max_process > 0:
        to_process = processable[:max_process]

    processed = 0
    errors = 0
    results = []

    # Process in concurrent batches
    for i in range(0, len(to_process), concurrency):
        batch = to_process[i:i + concurrency]

        async def _process_one(p):
            pid = p["id"]
            try:
                await process_payment_webhook(seller_slug, pid)
                return {"id": pid, "status": "ok"}
            except Exception as e:
                logger.error(f"Backfill error for payment {pid}: {e}")
                return {"id": pid, "status": "error", "error": str(e)}

        batch_results = await asyncio.gather(*[_process_one(p) for p in batch])

        for r in batch_results:
            results.append(r)
            if r["status"] == "ok":
                processed += 1
            else:
                errors += 1

        done = i + len(batch)
        logger.info(f"Backfill progress: {done}/{len(to_process)} ({processed} ok, {errors} err)")

        if done < len(to_process):
            await asyncio.sleep(0.3)

    return {
        "mode": "process",
        "seller": seller_slug,
        "period": f"{begin_date} to {end_date}",
        "total_found": len(all_payments),
        "already_done": len(already_done),
        "processed": processed,
        "errors": errors,
        "remaining": len(processable) - len(to_process),
        "results": results,
    }
