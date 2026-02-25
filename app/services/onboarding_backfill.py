"""
Onboarding backfill service.

Performs historical payment ingestion when a seller is activated in
"dashboard_ca" mode. Searches by money_release_date >= ca_start_date so that
every payment whose cash is released within the CA accounting window is
registered, even if the sale was approved before that window.

Key design decisions (from ONBOARDING-V2-PLANO.md):
  - range_field="money_release_date" for the ML search query
  - Competencia remains date_approved (processor handles this correctly)
  - Immediately enqueues baixas for money_release_date <= today after processing
  - Fully resumable: idempotency at payment/ca_jobs level prevents duplicates
  - Progress is persisted to sellers.ca_backfill_progress (jsonb) every batch
  - Multiple sellers can run simultaneously (independent background tasks)
"""
import asyncio
import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any

from app.db.supabase import get_db
from app.services import ml_api
from app.services.expense_classifier import classify_non_order_payment
from app.services.processor import process_payment_webhook

logger = logging.getLogger(__name__)

# Batch size for paging the ML payments search API
_PAGE_SIZE = 50

# How many processed payments before persisting progress to Supabase
_PROGRESS_PERSIST_INTERVAL = 50

# Small sleep between pages to avoid exhausting the ML rate limit
_PAGE_SLEEP_SECONDS = 0.3

# Small sleep between payments to stay well under ML per-second limits
_PAYMENT_SLEEP_EVERY_N = 10
_PAYMENT_SLEEP_SECONDS = 0.5


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------


async def run_onboarding_backfill(seller_slug: str) -> None:
    """Background task: backfill all ML payments by money_release_date.

    This function is intended to be launched as an asyncio.Task so it runs
    concurrently alongside other sellers' backfills without blocking the event
    loop.

    Algorithm:
      1. Load seller config; validate ca_start_date is set.
      2. Mark ca_backfill_status = "running", ca_backfill_started_at = now.
      3. Fetch ALL payments via search_payments(range_field="money_release_date",
         begin_date=ca_start_date, end_date=yesterday), paginating through all
         results with limit=50.
      4. Build a set of already-done payment IDs from payments + mp_expenses
         tables to skip (idempotent resume on retry).
      5. For each unseen payment:
         - With order_id  → process_payment_webhook(seller_slug, pid, payment_data)
         - Without order_id → classify_non_order_payment(db, seller_slug, payment)
      6. Persist progress counters to sellers.ca_backfill_progress every
         _PROGRESS_PERSIST_INTERVAL payments.
      7. After all payments are processed, trigger baixas for this seller to
         immediately settle parcelas whose money_release_date <= today.
      8. On success: ca_backfill_status = "completed", ca_backfill_completed_at = now.
      9. On any unhandled exception: ca_backfill_status = "failed" (preserves
         progress so a retry can continue where it left off).
    """
    db = get_db()

    # --- 1. Load seller config --------------------------------------------------
    seller_row = (
        db.table("sellers")
        .select(
            "slug, name, active, ca_start_date, ca_backfill_status, "
            "ca_conta_bancaria, ca_centro_custo_variavel, integration_mode"
        )
        .eq("slug", seller_slug)
        .limit(1)
        .execute()
    )
    if not seller_row.data:
        logger.error("OnboardingBackfill %s: seller not found, aborting", seller_slug)
        return

    seller = seller_row.data[0]
    ca_start_date_raw: str | None = seller.get("ca_start_date")

    if not ca_start_date_raw:
        logger.error(
            "OnboardingBackfill %s: ca_start_date is not set, aborting", seller_slug
        )
        return

    if seller.get("integration_mode") != "dashboard_ca":
        logger.error(
            "OnboardingBackfill %s: integration_mode=%r, expected dashboard_ca, aborting",
            seller_slug,
            seller.get("integration_mode"),
        )
        return

    # --- 2. Mark status = running -----------------------------------------------
    now_utc = datetime.now(timezone.utc)
    _update_seller_backfill_fields(
        db,
        seller_slug,
        {
            "ca_backfill_status": "running",
            "ca_backfill_started_at": now_utc.isoformat(),
        },
    )
    logger.info(
        "OnboardingBackfill %s: started (ca_start_date=%s)",
        seller_slug,
        ca_start_date_raw,
    )

    try:
        result = await _execute_backfill(db, seller_slug, ca_start_date_raw)

        # --- 8. Mark completed --------------------------------------------------
        _update_seller_backfill_fields(
            db,
            seller_slug,
            {
                "ca_backfill_status": "completed",
                "ca_backfill_completed_at": datetime.now(timezone.utc).isoformat(),
                "ca_backfill_progress": result,
            },
        )
        logger.info(
            "OnboardingBackfill %s: completed — total=%d processed=%d "
            "orders=%d expenses=%d skipped=%d errors=%d baixas=%d",
            seller_slug,
            result.get("total", 0),
            result.get("processed", 0),
            result.get("orders_processed", 0),
            result.get("expenses_classified", 0),
            result.get("skipped", 0),
            result.get("errors", 0),
            result.get("baixas_created", 0),
        )

    except Exception as exc:
        logger.error(
            "OnboardingBackfill %s: FAILED with unhandled exception: %s",
            seller_slug,
            exc,
            exc_info=True,
        )
        # --- 9. Mark failed (keep progress so retry can resume) -----------------
        try:
            _update_seller_backfill_fields(
                db,
                seller_slug,
                {"ca_backfill_status": "failed"},
            )
        except Exception as db_err:
            logger.warning(
                "OnboardingBackfill %s: could not persist 'failed' status: %s",
                seller_slug,
                db_err,
            )


async def retry_backfill(seller_slug: str) -> None:
    """Re-trigger a failed (or stuck) backfill for a seller.

    Resets the status to "pending" then immediately delegates to
    run_onboarding_backfill.  Because processing is idempotent
    (payments / mp_expenses upsert on conflict), this safely continues from
    where the previous run left off without duplicating any CA events.
    """
    db = get_db()
    seller_row = (
        db.table("sellers")
        .select("slug, ca_backfill_status, ca_start_date, integration_mode")
        .eq("slug", seller_slug)
        .limit(1)
        .execute()
    )
    if not seller_row.data:
        raise ValueError(f"Seller {seller_slug} not found")

    seller = seller_row.data[0]

    if seller.get("integration_mode") != "dashboard_ca":
        raise ValueError(
            f"Seller {seller_slug} is not in dashboard_ca mode "
            f"(current: {seller.get('integration_mode')})"
        )

    if not seller.get("ca_start_date"):
        raise ValueError(f"Seller {seller_slug} has no ca_start_date configured")

    current_status = seller.get("ca_backfill_status")
    if current_status == "running":
        logger.warning(
            "OnboardingBackfill %s: retry requested but status=running, "
            "a backfill is likely already in progress",
            seller_slug,
        )

    _update_seller_backfill_fields(db, seller_slug, {"ca_backfill_status": "pending"})
    logger.info(
        "OnboardingBackfill %s: retry triggered (previous status=%s)",
        seller_slug,
        current_status,
    )

    # Launch as a new background task so the caller is not blocked
    asyncio.create_task(run_onboarding_backfill(seller_slug))


def get_backfill_status(seller_slug: str) -> dict[str, Any]:
    """Return the current backfill status for a seller.

    Reads directly from the sellers table so the response always reflects the
    live state even during a running backfill.

    Returns:
        A dict with the following keys:
          - ca_backfill_status: "pending" | "running" | "completed" | "failed" | null
          - ca_backfill_started_at: ISO timestamp or null
          - ca_backfill_completed_at: ISO timestamp or null
          - ca_backfill_progress: progress dict or null
    """
    db = get_db()
    result = (
        db.table("sellers")
        .select(
            "ca_backfill_status, ca_backfill_started_at, "
            "ca_backfill_completed_at, ca_backfill_progress"
        )
        .eq("slug", seller_slug)
        .limit(1)
        .execute()
    )
    if not result.data:
        raise ValueError(f"Seller {seller_slug} not found")

    row = result.data[0]
    return {
        "ca_backfill_status": row.get("ca_backfill_status"),
        "ca_backfill_started_at": row.get("ca_backfill_started_at"),
        "ca_backfill_completed_at": row.get("ca_backfill_completed_at"),
        "ca_backfill_progress": row.get("ca_backfill_progress"),
    }


# ---------------------------------------------------------------------------
# Core backfill execution (private)
# ---------------------------------------------------------------------------


async def _execute_backfill(
    db,
    seller_slug: str,
    ca_start_date_raw: str,
) -> dict[str, Any]:
    """Orchestrate the full backfill for one seller.

    Returns the final progress dict which is persisted to ca_backfill_progress.
    """
    today: date = datetime.now(timezone.utc).date()

    # Extend the search window 90 days into the future to capture payments
    # approved before ca_start_date (e.g. late-month sales) whose
    # money_release_date falls after today.  Without this, those payments fall
    # through the cracks: the backfill end_date would be yesterday and the
    # daily sync only looks at D-1..D-3 by date_approved/date_last_updated,
    # which never covers old approved-but-not-yet-released payments.
    future_cutoff: date = today + timedelta(days=90)

    # Dates for ML API (BRT timezone to match the account_statement convention)
    begin_date = f"{ca_start_date_raw}T00:00:00.000-03:00"
    end_date = f"{future_cutoff.isoformat()}T23:59:59.999-03:00"

    # --- 3. Fetch all payments by money_release_date ----------------------------
    logger.info(
        "OnboardingBackfill %s: fetching payments "
        "(range_field=money_release_date, %s → %s, includes future releases up to +90d)",
        seller_slug,
        ca_start_date_raw,
        future_cutoff.isoformat(),
    )
    all_payments = await _fetch_all_payments(seller_slug, begin_date, end_date)
    total = len(all_payments)
    logger.info(
        "OnboardingBackfill %s: fetched %d payments total", seller_slug, total
    )

    # --- 4. Build already-done set (resumability) --------------------------------
    already_done_ids = _load_already_done(db, seller_slug)
    logger.info(
        "OnboardingBackfill %s: %d payments already processed (will skip)",
        seller_slug,
        len(already_done_ids),
    )

    # --- 5. Process each payment -------------------------------------------------
    progress: dict[str, Any] = {
        "total": total,
        "processed": 0,
        "orders_processed": 0,
        "expenses_classified": 0,
        "skipped": 0,
        "errors": 0,
        "baixas_created": 0,
        "last_payment_id": None,
    }

    processed_count = 0

    for payment in all_payments:
        pid: int = payment["id"]
        status: str = payment.get("status", "")

        # Skip terminal statuses immediately (never need CA events)
        if status in ("cancelled", "rejected"):
            progress["skipped"] += 1
            continue

        # Skip already-done (idempotent resume)
        if pid in already_done_ids:
            progress["skipped"] += 1
            continue

        order_id = (payment.get("order") or {}).get("id")
        op_type = payment.get("operation_type", "")

        if order_id:
            # ORDER payment → main processor
            # Skip non-sale variants (same filters as daily_sync)
            if payment.get("description") == "marketplace_shipment":
                progress["skipped"] += 1
                continue
            if (payment.get("collector") or {}).get("id") is not None:
                progress["skipped"] += 1
                continue
            if status not in ("approved", "refunded", "in_mediation", "charged_back"):
                progress["skipped"] += 1
                continue

            try:
                await process_payment_webhook(
                    seller_slug, pid, payment_data=payment
                )
                progress["orders_processed"] += 1
                progress["processed"] += 1
                progress["last_payment_id"] = pid
            except Exception as exc:
                logger.error(
                    "OnboardingBackfill %s: error processing order payment %d: %s",
                    seller_slug,
                    pid,
                    exc,
                )
                progress["errors"] += 1

        else:
            # NON-ORDER payment → classifier
            # Skip internal movements that we never store
            if op_type in ("partition_transfer", "payment_addition"):
                progress["skipped"] += 1
                continue

            if status != "approved":
                progress["skipped"] += 1
                continue

            try:
                result = await classify_non_order_payment(db, seller_slug, payment)
                if result:
                    progress["expenses_classified"] += 1
                    progress["processed"] += 1
                    progress["last_payment_id"] = pid
                else:
                    progress["skipped"] += 1
            except Exception as exc:
                logger.error(
                    "OnboardingBackfill %s: error classifying payment %d: %s",
                    seller_slug,
                    pid,
                    exc,
                )
                progress["errors"] += 1

        processed_count += 1

        # Persist progress every _PROGRESS_PERSIST_INTERVAL payments
        if processed_count % _PROGRESS_PERSIST_INTERVAL == 0:
            _update_seller_backfill_fields(
                db, seller_slug, {"ca_backfill_progress": progress}
            )
            logger.info(
                "OnboardingBackfill %s: checkpoint — processed=%d/%d "
                "orders=%d expenses=%d errors=%d",
                seller_slug,
                progress["processed"],
                total,
                progress["orders_processed"],
                progress["expenses_classified"],
                progress["errors"],
            )

        # Yield control to the event loop occasionally to avoid starving other tasks
        if processed_count % _PAYMENT_SLEEP_EVERY_N == 0:
            await asyncio.sleep(_PAYMENT_SLEEP_SECONDS)

    # --- 7. Trigger baixas for parcelas with vencimento <= today ----------------
    # Decision 22 from ONBOARDING-V2-PLANO.md: create baixas immediately for
    # money_release_date <= today so that at the end of backfill all vencidas
    # parcelas are already settled in CA.
    # We reuse processar_baixas_auto which already handles release verification
    # and idempotency via ca_queue.enqueue_baixa.
    try:
        baixas_result = await _trigger_baixas(seller_slug)
        progress["baixas_created"] = baixas_result.get("queued", 0)
        logger.info(
            "OnboardingBackfill %s: baixas triggered — queued=%d skipped=%d",
            seller_slug,
            baixas_result.get("queued", 0),
            baixas_result.get("skipped", 0),
        )
    except Exception as exc:
        logger.warning(
            "OnboardingBackfill %s: baixas trigger failed (non-fatal): %s",
            seller_slug,
            exc,
        )

    return progress


async def _fetch_all_payments(
    seller_slug: str,
    begin_date: str,
    end_date: str,
) -> list[dict]:
    """Paginate through all ML payments for the backfill window.

    Uses range_field="money_release_date" so we capture every payment whose
    cash release falls within the CA accounting window (ca_start_date → yesterday),
    regardless of when the sale was approved.

    Deduplicates by payment_id (last page wins on any collision, which is safe
    because each payment_id is unique in the ML API).
    """
    payments_by_id: dict[int, dict] = {}
    offset = 0

    while True:
        try:
            result = await ml_api.search_payments(
                seller_slug,
                begin_date,
                end_date,
                offset=offset,
                limit=_PAGE_SIZE,
                range_field="money_release_date",
            )
        except Exception as exc:
            logger.error(
                "OnboardingBackfill %s: search_payments failed at offset=%d: %s",
                seller_slug,
                offset,
                exc,
            )
            raise

        batch: list[dict] = result.get("results", [])
        total: int = result.get("paging", {}).get("total", 0)

        for payment in batch:
            payments_by_id[payment["id"]] = payment

        offset += len(batch)
        logger.debug(
            "OnboardingBackfill %s: page fetched %d payments (offset=%d total=%d)",
            seller_slug,
            len(batch),
            offset,
            total,
        )

        if offset >= total or not batch:
            break

        await asyncio.sleep(_PAGE_SLEEP_SECONDS)

    # Return sorted by money_release_date asc, then payment_id asc, for determinism
    return sorted(
        payments_by_id.values(),
        key=lambda p: (
            (p.get("money_release_date") or p.get("date_approved") or ""),
            int(p.get("id") or 0),
        ),
    )


def _load_already_done(db, seller_slug: str) -> set[int]:
    """Load all payment IDs that have already been processed for this seller.

    Reads from both the payments table (order payments) and the mp_expenses
    table (non-order payments).  This is the resumability mechanism: on a retry
    the backfill simply skips everything already in Supabase.

    Only statuses that represent completed processing are considered done.
    Payments with status "pending" or "queued" are re-evaluated because they
    may not have been fully processed yet.
    """
    done: set[int] = set()

    # From payments table (order payments already sent to CA queue)
    page_start = 0
    page_limit = 1000
    while True:
        rows = (
            db.table("payments")
            .select("ml_payment_id, status")
            .eq("seller_slug", seller_slug)
            .in_("status", ["synced", "queued", "refunded", "skipped", "skipped_non_sale"])
            .range(page_start, page_start + page_limit - 1)
            .execute()
        )
        batch = rows.data or []
        for row in batch:
            pid_raw = row.get("ml_payment_id")
            if pid_raw is not None:
                try:
                    done.add(int(pid_raw))
                except (TypeError, ValueError):
                    pass
        if len(batch) < page_limit:
            break
        page_start += page_limit

    # Includes both API-originated (source=payments_api) and extrato (source=extrato) lines from onboarding.
    # Numeric payment_ids (e.g. "123456789") are added to done; composite extrato keys
    # (e.g. "123456789:df") raise ValueError and are silently ignored — they use a separate
    # idempotency mechanism inside process_extrato_csv_text (check-before-insert with composite keys).
    # From mp_expenses table (non-order payments already classified)
    page_start = 0
    while True:
        rows = (
            db.table("mp_expenses")
            .select("payment_id")
            .eq("seller_slug", seller_slug)
            .range(page_start, page_start + page_limit - 1)
            .execute()
        )
        batch = rows.data or []
        for row in batch:
            pid_raw = row.get("payment_id")
            if pid_raw is not None:
                try:
                    done.add(int(pid_raw))
                except (TypeError, ValueError):
                    pass
        if len(batch) < page_limit:
            break
        page_start += page_limit

    return done


async def _trigger_baixas(seller_slug: str) -> dict[str, Any]:
    """Trigger baixas processing for a seller after the backfill completes.

    Reuses the same logic as the daily baixas scheduler by calling
    processar_baixas_auto from app.routers.baixas.  This ensures
    release verification (money_release_status == "released") is
    performed before each baixa, and idempotency is respected via
    ca_queue.enqueue_baixa.

    The import is done at call time to avoid circular imports (baixas.py
    imports from ca_api and ca_queue; this module imports from processor.py).
    """
    from app.routers.baixas import processar_baixas_auto

    return await processar_baixas_auto(seller_slug)


def _update_seller_backfill_fields(db, seller_slug: str, fields: dict) -> None:
    """Update one or more backfill-related columns on the sellers row.

    Silently logs and swallows DB errors so that a transient Supabase failure
    in progress-tracking does not abort the backfill itself.
    """
    try:
        db.table("sellers").update(fields).eq("slug", seller_slug).execute()
    except Exception as exc:
        logger.warning(
            "OnboardingBackfill %s: failed to update seller fields %s: %s",
            seller_slug,
            list(fields.keys()),
            exc,
        )
