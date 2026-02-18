"""
Daily sync service: replaces webhooks as primary payment ingestion mechanism.
Runs at 00:01 BRT, covers D-1 to D-3 to catch any delayed payments.
Orders → processor.py (receita + comissao + frete in CA)
Non-orders → expense_classifier.py OR deferred to legacy bridge (env mode)
"""
import asyncio
import logging
from datetime import date, datetime, timedelta, timezone

from app.config import settings
from app.db.supabase import get_db
from app.models.sellers import get_all_active_sellers
from app.services import ml_api
from app.services.processor import process_payment_webhook
from app.services.expense_classifier import classify_non_order_payment

logger = logging.getLogger(__name__)

BRT = timezone(timedelta(hours=-3))
SYNC_CURSOR_KEY = "daily_sync_payments"
CURSOR_OVERLAP_DAYS = 1

_sync_state_table_available: bool | None = None


def _parse_date_yyyy_mm_dd(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return datetime.strptime(value[:10], "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def _sync_state_available(db) -> bool:
    global _sync_state_table_available
    if _sync_state_table_available is not None:
        return _sync_state_table_available
    try:
        db.table("sync_state").select("sync_key").limit(1).execute()
        _sync_state_table_available = True
    except Exception:
        _sync_state_table_available = False
    return _sync_state_table_available


def _load_sync_cursor(db, seller_slug: str) -> dict | None:
    if not _sync_state_available(db):
        return None
    try:
        result = db.table("sync_state").select("state").eq(
            "sync_key", SYNC_CURSOR_KEY
        ).eq("seller_slug", seller_slug).limit(1).execute()
        if not result.data:
            return None
        state = result.data[0].get("state") or {}
        return state if isinstance(state, dict) else None
    except Exception as e:
        logger.warning("DailySync %s: failed to load sync cursor: %s", seller_slug, e)
        return None


def _persist_sync_cursor(
    db,
    seller_slug: str,
    begin_date: str,
    end_date: str,
    result: dict,
) -> bool:
    if not _sync_state_available(db):
        return False

    now = datetime.now(timezone.utc).isoformat()
    state = {
        "last_begin_date": begin_date,
        "last_end_date": end_date,
        "last_success_at": now,
        "last_result": {
            "total_fetched": result.get("total_fetched", 0),
            "orders_processed": result.get("orders_processed", 0),
            "orders_reprocessed_updates": result.get("orders_reprocessed_updates", 0),
            "expenses_classified": result.get("expenses_classified", 0),
            "skipped": result.get("skipped", 0),
            "errors": result.get("errors", 0),
        },
    }
    try:
        db.table("sync_state").upsert({
            "sync_key": SYNC_CURSOR_KEY,
            "seller_slug": seller_slug,
            "state": state,
            "updated_at": now,
        }, on_conflict="sync_key,seller_slug").execute()
        return True
    except Exception as e:
        logger.warning("DailySync %s: failed to persist sync cursor: %s", seller_slug, e)
        return False


def _compute_sync_window(
    now_brt: datetime,
    lookback_days: int,
    cursor_state: dict | None,
) -> tuple[str, str, str]:
    end_dt = (now_brt - timedelta(days=1)).date()
    begin_dt = (now_brt - timedelta(days=lookback_days)).date()
    source = "lookback"

    if cursor_state:
        last_end = _parse_date_yyyy_mm_dd(cursor_state.get("last_end_date"))
        if last_end:
            # Re-open one day from cursor to catch late updates without gaps.
            cursor_begin = last_end - timedelta(days=CURSOR_OVERLAP_DAYS)
            if cursor_begin < begin_dt:
                begin_dt = cursor_begin
                source = "cursor+lookback"

    if begin_dt > end_dt:
        begin_dt = end_dt

    return begin_dt.isoformat(), end_dt.isoformat(), source


async def _fetch_payments_by_range(
    seller_slug: str,
    begin: str,
    end_dt: str,
    range_field: str,
    page_size: int = 50,
) -> list[dict]:
    """Fetch all MP payments for a seller/date window using a specific range field."""
    all_payments = []
    offset = 0

    while True:
        result = await ml_api.search_payments(
            seller_slug, begin, end_dt, offset, page_size, range_field=range_field
        )
        payments = result.get("results", [])
        total = result.get("paging", {}).get("total", 0)

        all_payments.extend(payments)
        offset += len(payments)

        if offset >= total or not payments:
            break

        await asyncio.sleep(0.3)

    return all_payments


async def _daily_sync_scheduler():
    """Async loop that triggers sync at 00:01 BRT daily.
    Pattern identical to _daily_baixa_scheduler in main.py."""
    try:
        from zoneinfo import ZoneInfo
    except ImportError:
        from backports.zoneinfo import ZoneInfo

    brt = ZoneInfo("America/Sao_Paulo")
    target_hour = 0
    target_minute = 1

    while True:
        now_brt = datetime.now(brt)
        # Calculate next 00:01 BRT
        if now_brt.hour == 0 and now_brt.minute < target_minute:
            target = now_brt.replace(hour=target_hour, minute=target_minute, second=0, microsecond=0)
        else:
            target = (now_brt + timedelta(days=1)).replace(
                hour=target_hour, minute=target_minute, second=0, microsecond=0
            )

        wait_seconds = (target - now_brt).total_seconds()
        logger.info(f"DailySync: next run in {wait_seconds:.0f}s ({target.isoformat()})")

        await asyncio.sleep(wait_seconds)

        try:
            results = await sync_all_sellers()
            logger.info(f"DailySync completed: {len(results)} sellers processed")
            for r in results:
                logger.info(
                    f"DailySync {r['seller']}: orders={r['orders_processed']} "
                    f"expenses={r['expenses_classified']} skipped={r['skipped']} "
                    f"errors={r['errors']}"
                )
        except Exception as e:
            logger.error(f"DailySync error: {e}", exc_info=True)


async def sync_all_sellers(lookback_days: int = 3) -> list[dict]:
    """Sync payments for all active sellers, covering D-1 to D-{lookback_days}.

    Returns list of result dicts per seller.
    """
    db = get_db()
    sellers = get_all_active_sellers(db)

    now_brt = datetime.now(BRT)

    results = []
    for seller in sellers:
        slug = seller["slug"]
        cursor_state = _load_sync_cursor(db, slug)
        begin_date, end_date, window_source = _compute_sync_window(
            now_brt, lookback_days, cursor_state
        )
        try:
            result = await sync_seller_payments(slug, begin_date, end_date)
            result["window_source"] = window_source
            result["cursor_last_end_date"] = (
                (cursor_state or {}).get("last_end_date")
            )
            if result.get("errors", 0) == 0:
                result["cursor_updated"] = _persist_sync_cursor(
                    db, slug, begin_date, end_date, result
                )
            else:
                result["cursor_updated"] = False
            results.append(result)
        except Exception as e:
            logger.error(f"DailySync error for {slug}: {e}", exc_info=True)
            results.append({
                "seller": slug,
                "period": f"{begin_date} to {end_date}",
                "window_source": window_source,
                "orders_processed": 0,
                "expenses_classified": 0,
                "skipped": 0,
                "errors": 1,
                "error_detail": str(e),
                "cursor_updated": False,
            })
        # Small delay between sellers to avoid rate limits
        await asyncio.sleep(1)

    return results


async def sync_seller_payments(seller_slug: str, begin_date: str, end_date: str) -> dict:
    """Sync all payments for a seller in a date range.

    1. Fetches all payments via search API (date_approved + date_last_updated)
    2. Deduplicates by payment_id and detects status changes
    3. Orders → process_payment_webhook (with pre-fetched payment_data)
    4. Non-orders → classify_non_order_payment OR defer to legacy mode

    Returns summary dict.
    """
    db = get_db()
    non_order_mode = (settings.daily_sync_non_order_mode or "classifier").strip().lower()
    if non_order_mode not in {"classifier", "legacy"}:
        logger.warning(
            "DailySync %s: invalid daily_sync_non_order_mode=%r, falling back to classifier",
            seller_slug,
            non_order_mode,
        )
        non_order_mode = "classifier"

    # Format dates for MP API (BRT timezone)
    begin = f"{begin_date}T00:00:00.000-03:00"
    end_dt = f"{end_date}T23:59:59.999-03:00"

    # 1. Fetch by approval date (new sales) and by last update (refunds/chargebacks/mediations)
    by_approved = await _fetch_payments_by_range(
        seller_slug, begin, end_dt, range_field="date_approved"
    )
    by_updated = await _fetch_payments_by_range(
        seller_slug, begin, end_dt, range_field="date_last_updated"
    )

    # Deduplicate by payment_id (last_updated fetch wins on collision)
    payments_by_id: dict[int, dict] = {}
    for payment in by_approved:
        payments_by_id[payment["id"]] = payment
    for payment in by_updated:
        payments_by_id[payment["id"]] = payment

    all_payments = sorted(
        payments_by_id.values(),
        key=lambda p: (
            p.get("date_last_updated") or p.get("date_approved") or p.get("date_created") or "",
            int(p.get("id") or 0),
        ),
    )
    logger.info(
        f"DailySync {seller_slug}: fetched approved={len(by_approved)} "
        f"updated={len(by_updated)} unique={len(all_payments)} "
        f"({begin_date} to {end_date})"
    )

    # 2. Load existing order payments (for status change detection)
    existing_orders: dict[int, dict] = {}
    page_start = 0
    page_limit = 1000
    while True:
        done_result = db.table("payments").select(
            "ml_payment_id, ml_status, status, raw_payment"
        ).eq(
            "seller_slug", seller_slug
        ).range(page_start, page_start + page_limit - 1).execute()
        batch = done_result.data or []
        for row in batch:
            ml_payment_id = row.get("ml_payment_id")
            if ml_payment_id is None:
                continue
            raw = row.get("raw_payment") or {}
            existing_orders[int(ml_payment_id)] = {
                "ml_status": row.get("ml_status"),
                "processor_status": row.get("status"),
                "status_detail": raw.get("status_detail"),
            }
        if len(batch) < page_limit:
            break
        page_start += page_limit

    orders_processed = 0
    expenses_classified = 0
    non_orders_deferred_to_legacy = 0
    skipped = 0
    errors = 0
    reprocessed_updates = 0

    for payment in all_payments:
        pid = payment["id"]
        status = payment.get("status", "")

        # Skip terminal statuses
        if status in ("cancelled", "rejected"):
            skipped += 1
            continue

        order_id = (payment.get("order") or {}).get("id")
        op_type = payment.get("operation_type", "")

        if order_id:
            # ORDER payment → processor
            existing = existing_orders.get(pid)
            status_detail = payment.get("status_detail")
            should_reprocess = False
            if existing:
                if existing.get("ml_status") != status:
                    should_reprocess = True
                elif existing.get("status_detail") != status_detail:
                    should_reprocess = True
                elif existing.get("processor_status") in ("pending", "queued"):
                    # Keep pushing unresolved items until processor settles them.
                    should_reprocess = True

            if existing and not should_reprocess:
                skipped += 1
                continue

            # Skip non-sale order payments
            if payment.get("description") == "marketplace_shipment":
                skipped += 1
                continue
            if (payment.get("collector") or {}).get("id") is not None:
                skipped += 1
                continue
            if status not in ("approved", "refunded", "in_mediation", "charged_back"):
                skipped += 1
                continue

            try:
                await process_payment_webhook(seller_slug, pid, payment_data=payment)
                orders_processed += 1
                if existing and should_reprocess:
                    reprocessed_updates += 1
            except Exception as e:
                logger.error(f"DailySync error processing order payment {pid}: {e}")
                errors += 1

        else:
            # NON-ORDER payment → classifier
            if non_order_mode == "legacy":
                non_orders_deferred_to_legacy += 1
                skipped += 1
                continue

            # Skip internal movements that we don't even store
            if op_type in ("partition_transfer", "payment_addition"):
                skipped += 1
                continue

            if status != "approved":
                skipped += 1
                continue

            try:
                result = await classify_non_order_payment(db, seller_slug, payment)
                if result:
                    expenses_classified += 1
                else:
                    skipped += 1
            except Exception as e:
                logger.error(f"DailySync error classifying payment {pid}: {e}")
                errors += 1

        # Rate limit: small delay every 10 payments
        if (orders_processed + expenses_classified) % 10 == 0 and (orders_processed + expenses_classified) > 0:
            await asyncio.sleep(0.5)

    logger.info(
        f"DailySync {seller_slug} done: orders={orders_processed} "
        f"reprocessed={reprocessed_updates} "
        f"expenses={expenses_classified} deferred_non_orders={non_orders_deferred_to_legacy} "
        f"skipped={skipped} errors={errors} mode={non_order_mode}"
    )

    return {
        "seller": seller_slug,
        "period": f"{begin_date} to {end_date}",
        "non_order_mode": non_order_mode,
        "total_fetched": len(all_payments),
        "fetched_date_approved": len(by_approved),
        "fetched_date_last_updated": len(by_updated),
        "existing_orders": len(existing_orders),
        "orders_processed": orders_processed,
        "orders_reprocessed_updates": reprocessed_updates,
        "expenses_classified": expenses_classified,
        "non_orders_deferred_to_legacy": non_orders_deferred_to_legacy,
        "skipped": skipped,
        "errors": errors,
    }
