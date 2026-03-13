#!/usr/bin/env python3
"""
One-time migration: mp_expenses → expense lifecycle events in the event ledger.

For each mp_expense record WITHOUT a matching expense_captured event:
  1. Creates expense_captured event with full metadata
  2. If auto_categorized: creates expense_classified event
  3. If status == 'exported': creates expense_exported event (batch_id='legacy_migration')
  4. If status == 'manually_categorized': creates expense_reviewed event

Fully idempotent — safe to run multiple times (ON CONFLICT DO NOTHING).

Usage:
    cd "/Volumes/SSD Eryk/LeverMoney"
    python3 testes/migrate_mp_expenses_to_ledger.py --all
    python3 testes/migrate_mp_expenses_to_ledger.py --seller 141air
    python3 testes/migrate_mp_expenses_to_ledger.py --all --dry-run
"""
import asyncio
import argparse
import logging
import sys
from collections import Counter
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.db.supabase import get_db
from app.services.event_ledger import record_expense_event, EventRecordError

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("migrate_mp_expenses")

PAGE_SIZE = 500


def _paginate_mp_expenses(db, seller_slug: str | None = None) -> list[dict]:
    """Fetch all mp_expenses rows, paginated."""
    all_rows: list[dict] = []
    page_start = 0
    while True:
        q = db.table("mp_expenses").select("*").order("created_at")
        if seller_slug:
            q = q.eq("seller_slug", seller_slug)
        result = q.range(page_start, page_start + PAGE_SIZE - 1).execute()
        rows = result.data or []
        all_rows.extend(rows)
        if len(rows) < PAGE_SIZE:
            break
        page_start += PAGE_SIZE
    return all_rows


def _get_existing_captured_keys(db, seller_slug: str | None = None) -> set[str]:
    """Fetch all existing expense_captured idempotency keys from payment_events."""
    keys: set[str] = set()
    page_start = 0
    while True:
        q = db.table("payment_events").select("idempotency_key").eq(
            "event_type", "expense_captured"
        )
        if seller_slug:
            q = q.eq("seller_slug", seller_slug)
        result = q.range(page_start, page_start + PAGE_SIZE - 1).execute()
        rows = result.data or []
        for r in rows:
            keys.add(r["idempotency_key"])
        if len(rows) < PAGE_SIZE:
            break
        page_start += PAGE_SIZE
    return keys


def _compute_signed_amount(exp: dict) -> float:
    """Compute signed amount: positive for income, negative for expense/transfer."""
    amount = abs(float(exp.get("amount") or 0))
    direction = exp.get("expense_direction", "expense")
    if direction == "income":
        return amount
    return -amount


def _extract_competencia_date(exp: dict) -> str:
    """Extract competencia date from date_approved or date_created."""
    raw = exp.get("date_approved") or exp.get("date_created") or ""
    return raw[:10] if raw else "1970-01-01"


def _build_metadata(exp: dict) -> dict:
    """Build metadata dict from mp_expenses row (matches expense_classifier format)."""
    return {
        "expense_type": exp.get("expense_type", "unknown"),
        "expense_direction": exp.get("expense_direction", "expense"),
        "ca_category": exp.get("ca_category"),
        "auto_categorized": exp.get("auto_categorized", False),
        "description": exp.get("description"),
        "amount": exp.get("amount"),
        "date_created": exp.get("date_created"),
        "date_approved": exp.get("date_approved"),
        "business_branch": exp.get("business_branch"),
        "operation_type": exp.get("operation_type"),
        "payment_method": exp.get("payment_method"),
        "external_reference": exp.get("external_reference"),
        "febraban_code": exp.get("febraban_code"),
        "beneficiary_name": exp.get("beneficiary_name"),
        "notes": exp.get("notes"),
        "source": "legacy_migration",
        "original_source": exp.get("source"),
    }


async def migrate_one(exp: dict, dry_run: bool, stats: Counter) -> None:
    """Migrate a single mp_expense record to the event ledger."""
    seller = exp["seller_slug"]
    payment_id = str(exp["payment_id"])
    expense_type = exp.get("expense_type", "unknown")
    status = exp.get("status", "pending_review")

    signed = _compute_signed_amount(exp)
    competencia = _extract_competencia_date(exp)
    metadata = _build_metadata(exp)

    if dry_run:
        logger.info("[DRY-RUN] Would migrate %s/%s (status=%s)", seller, payment_id, status)
        stats["would_migrate"] += 1
        return

    # 1. expense_captured
    try:
        await record_expense_event(
            seller_slug=seller,
            payment_id=payment_id,
            event_type="expense_captured",
            signed_amount=signed,
            competencia_date=competencia,
            expense_type=expense_type,
            metadata=metadata,
        )
        stats["captured"] += 1
    except EventRecordError as e:
        logger.error("Failed expense_captured for %s/%s: %s", seller, payment_id, e)
        stats["errors"] += 1
        return

    # 2. expense_classified (if auto_categorized)
    if exp.get("auto_categorized"):
        try:
            await record_expense_event(
                seller_slug=seller,
                payment_id=payment_id,
                event_type="expense_classified",
                signed_amount=0,
                competencia_date=competencia,
                expense_type=expense_type,
                metadata={"ca_category": exp.get("ca_category")},
            )
            stats["classified"] += 1
        except EventRecordError as e:
            logger.warning("Failed expense_classified for %s/%s: %s", seller, payment_id, e)

    # 3. expense_exported (if status == 'exported')
    if status == "exported":
        try:
            await record_expense_event(
                seller_slug=seller,
                payment_id=payment_id,
                event_type="expense_exported",
                signed_amount=0,
                competencia_date=competencia,
                expense_type=expense_type,
                metadata={
                    "batch_id": "legacy_migration",
                    "exported_at": exp.get("exported_at"),
                },
            )
            stats["exported"] += 1
        except EventRecordError as e:
            logger.warning("Failed expense_exported for %s/%s: %s", seller, payment_id, e)

    # 4. expense_reviewed (if status == 'manually_categorized')
    if status == "manually_categorized":
        try:
            await record_expense_event(
                seller_slug=seller,
                payment_id=payment_id,
                event_type="expense_reviewed",
                signed_amount=0,
                competencia_date=competencia,
                expense_type=expense_type,
                metadata={"source": "legacy_migration"},
            )
            stats["reviewed"] += 1
        except EventRecordError as e:
            logger.warning("Failed expense_reviewed for %s/%s: %s", seller, payment_id, e)


async def migrate(seller_slug: str | None, dry_run: bool) -> None:
    """Run the full migration."""
    db = get_db()

    # 1. Fetch all mp_expenses
    logger.info("Fetching mp_expenses%s...", f" for {seller_slug}" if seller_slug else "")
    expenses = _paginate_mp_expenses(db, seller_slug)
    logger.info("Found %d mp_expenses records", len(expenses))

    if not expenses:
        logger.info("Nothing to migrate")
        return

    # 2. Fetch existing expense_captured keys to skip already-migrated
    logger.info("Fetching existing expense_captured events...")
    existing_keys = _get_existing_captured_keys(db, seller_slug)
    logger.info("Found %d existing expense_captured events", len(existing_keys))

    # 3. Filter to only unmigrated expenses
    to_migrate = []
    for exp in expenses:
        idem_key = f"{exp['seller_slug']}:{exp['payment_id']}:expense_captured"
        if idem_key not in existing_keys:
            to_migrate.append(exp)

    logger.info("%d expenses need migration (%d already migrated)",
                len(to_migrate), len(expenses) - len(to_migrate))

    if not to_migrate:
        logger.info("All expenses already migrated — idempotent, 0 new events")
        return

    # 4. Migrate each expense
    stats: Counter = Counter()
    for i, exp in enumerate(to_migrate, 1):
        await migrate_one(exp, dry_run, stats)
        if i % 100 == 0:
            logger.info("Progress: %d/%d", i, len(to_migrate))

    # 5. Summary
    logger.info("=== Migration Complete ===")
    for key, count in sorted(stats.items()):
        logger.info("  %s: %d", key, count)

    # 6. Validation
    if not dry_run:
        logger.info("=== Validation ===")
        validate(db, seller_slug)


def validate(db, seller_slug: str | None = None) -> None:
    """Compare mp_expenses counts vs expense_captured counts per seller."""
    # Count mp_expenses per seller
    q1 = db.table("mp_expenses").select("seller_slug", count="exact")
    if seller_slug:
        q1 = q1.eq("seller_slug", seller_slug)

    # We need to do a grouped count manually via pagination
    mp_counts: Counter = Counter()
    page_start = 0
    while True:
        q = db.table("mp_expenses").select("seller_slug")
        if seller_slug:
            q = q.eq("seller_slug", seller_slug)
        result = q.range(page_start, page_start + PAGE_SIZE - 1).execute()
        rows = result.data or []
        for r in rows:
            mp_counts[r["seller_slug"]] += 1
        if len(rows) < PAGE_SIZE:
            break
        page_start += PAGE_SIZE

    # Count expense_captured per seller
    ev_counts: Counter = Counter()
    page_start = 0
    while True:
        q = db.table("payment_events").select("seller_slug").eq(
            "event_type", "expense_captured"
        )
        if seller_slug:
            q = q.eq("seller_slug", seller_slug)
        result = q.range(page_start, page_start + PAGE_SIZE - 1).execute()
        rows = result.data or []
        for r in rows:
            ev_counts[r["seller_slug"]] += 1
        if len(rows) < PAGE_SIZE:
            break
        page_start += PAGE_SIZE

    all_ok = True
    all_sellers = sorted(set(mp_counts.keys()) | set(ev_counts.keys()))
    for s in all_sellers:
        mp = mp_counts.get(s, 0)
        ev = ev_counts.get(s, 0)
        match = "OK" if mp == ev else "MISMATCH"
        if mp != ev:
            all_ok = False
        logger.info("  %s: mp_expenses=%d  expense_captured=%d  [%s]", s, mp, ev, match)

    if all_ok:
        logger.info("Validation PASSED: all counts match")
    else:
        logger.warning("Validation FAILED: some counts do not match")


def main():
    parser = argparse.ArgumentParser(description="Migrate mp_expenses to event ledger")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--seller", help="Migrate a specific seller")
    group.add_argument("--all", action="store_true", help="Migrate all sellers")
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing")
    parser.add_argument("--validate-only", action="store_true", help="Only run validation")
    args = parser.parse_args()

    seller = args.seller if args.seller else None

    if args.validate_only:
        db = get_db()
        validate(db, seller)
        return

    asyncio.run(migrate(seller, args.dry_run))


if __name__ == "__main__":
    main()
