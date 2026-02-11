"""
Backfill: puxa payments ML retroativos e processa no CA.
GET /backfill/{seller_slug}?begin_date=2026-02-01&end_date=2026-02-11&dry_run=true
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
):
    """
    Puxa payments do ML por período e processa no CA.
    Use dry_run=true primeiro para ver o que será processado.
    """
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

        await asyncio.sleep(0.5)

    # Summary by status
    status_counts = {}
    for p in all_payments:
        s = p.get("status", "unknown")
        status_counts[s] = status_counts.get(s, 0) + 1

    total_amount = sum(p.get("transaction_amount", 0) for p in all_payments)

    # Check which are already synced in Supabase
    already_synced = set()
    synced_result = db.table("payments").select("ml_payment_id").eq(
        "seller_slug", seller_slug
    ).eq("status", "synced").execute()
    if synced_result.data:
        already_synced = {r["ml_payment_id"] for r in synced_result.data}

    processable = [
        p for p in all_payments
        if p.get("status") in ("approved", "refunded") and p["id"] not in already_synced
    ]

    if dry_run:
        return {
            "mode": "dry_run",
            "seller": seller_slug,
            "period": f"{begin_date} to {end_date}",
            "total_payments": len(all_payments),
            "total_amount": round(total_amount, 2),
            "by_status": status_counts,
            "already_synced": len(already_synced),
            "to_process": len(processable),
            "sample": [
                {
                    "id": p["id"],
                    "status": p["status"],
                    "amount": p["transaction_amount"],
                    "date": (p.get("date_approved") or p.get("date_created", ""))[:19],
                    "order_id": p.get("order", {}).get("id") if p.get("order") else None,
                    "fees": sum(f.get("amount", 0) for f in p.get("fee_details", [])),
                    "net": p.get("transaction_details", {}).get("net_received_amount"),
                }
                for p in all_payments[:20]
            ],
        }

    # === PROCESS MODE ===
    to_process = processable
    if max_process > 0:
        to_process = processable[:max_process]

    processed = 0
    errors = 0
    results = []

    for p in to_process:
        payment_id = p["id"]
        try:
            await process_payment_webhook(seller_slug, payment_id)
            processed += 1
            results.append({"id": payment_id, "status": "ok"})
            logger.info(f"Backfill processed payment {payment_id} ({processed}/{len(to_process)})")
        except Exception as e:
            errors += 1
            results.append({"id": payment_id, "status": "error", "error": str(e)})
            logger.error(f"Backfill error for payment {payment_id}: {e}")

        # Rate limit: ~1 payment per 2 seconds (each payment makes ~10 API calls)
        await asyncio.sleep(2.0)

    return {
        "mode": "process",
        "seller": seller_slug,
        "period": f"{begin_date} to {end_date}",
        "total_found": len(all_payments),
        "already_synced": len(already_synced),
        "processed": processed,
        "errors": errors,
        "remaining": len(processable) - len(to_process),
        "results": results,
    }
