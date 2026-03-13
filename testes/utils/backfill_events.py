#!/usr/bin/env python3
"""
Backfill payment_events from existing payments table.

Reads all payments from Supabase and emits retroactive events into the
payment_events ledger. Fully idempotent — safe to run multiple times.

Usage:
    cd "/Volumes/SSD Eryk/LeverMoney"
    python3 testes/utils/backfill_events.py --seller 141air
    python3 testes/utils/backfill_events.py --all
    python3 testes/utils/backfill_events.py --all --dry-run
"""
import asyncio
import argparse
import logging
import sys
from collections import Counter
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.db.supabase import get_db
from app.services.processor import _to_brt_date, _to_float
from app.services.event_ledger import record_event, build_idempotency_key, validate_event

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("backfill_events")

# Statuses that had a sale_approved (receita created)
SALE_STATUSES = {"queued", "synced", "refunded"}

# Page size for Supabase queries
PAGE_SIZE = 500


def _extract_refund_info(payment: dict) -> tuple[float, str, float, float]:
    """Extract refund amount, date, refunded_fee, refunded_shipping from payment.

    Returns (estorno_receita, date_refunded, refunded_fee, refunded_shipping).
    """
    amount = _to_float(payment.get("transaction_amount"))
    refunds = payment.get("refunds") or []

    if refunds:
        total_refunded_raw = sum(_to_float(r.get("amount", 0)) for r in refunds)
        date_refunded = (refunds[-1].get("date_created") or "")[:10]
    else:
        total_refunded_raw = _to_float(payment.get("transaction_amount_refunded")) or amount
        date_refunded = ""

    estorno_receita = min(total_refunded_raw, amount)

    # Extract refunded fees/shipping from charges_details
    refunded_fee = 0.0
    refunded_shipping = 0.0
    has_charges = False

    for charge in payment.get("charges_details") or []:
        accounts = charge.get("accounts") or {}
        if accounts.get("from") != "collector":
            continue

        charge_type = str(charge.get("type") or "").lower()
        charge_name = str(charge.get("name") or "").strip().lower()
        if charge_name == "financing_fee":
            continue

        refunded_val = _to_float((charge.get("amounts") or {}).get("refunded"))
        if charge_type == "fee":
            refunded_fee += refunded_val
            has_charges = True
        elif charge_type == "shipping":
            refunded_shipping += refunded_val
            has_charges = True

    if not has_charges:
        net = _to_float((payment.get("transaction_details") or {}).get("net_received_amount"))
        total_fees = round(amount - net, 2) if net > 0 else 0
        refunded_fee = total_fees

    return estorno_receita, date_refunded, round(refunded_fee, 2), round(refunded_shipping, 2)


async def backfill_payment(row: dict, stats: Counter, dry_run: bool = False):
    """Emit events for a single payment row from Supabase."""
    seller_slug = row["seller_slug"]
    payment_id = row["ml_payment_id"]
    status = row["status"]
    raw = row.get("raw_payment") or {}

    # Derive dates
    date_approved_raw = raw.get("date_approved") or raw.get("date_created") or ""
    competencia = _to_brt_date(date_approved_raw) if date_approved_raw else None
    if not competencia or len(competencia) < 10:
        stats["skipped_no_date"] += 1
        return

    order_id = (raw.get("order") or {}).get("id")
    amount = _to_float(raw.get("transaction_amount"))
    processor_fee = _to_float(row.get("processor_fee"))
    processor_shipping = _to_float(row.get("processor_shipping"))
    money_release_date = (row.get("money_release_date") or "")[:10] if row.get("money_release_date") else None

    events_to_emit = []

    # 1. Sale events (for payments that went through _process_approved)
    if status in SALE_STATUSES:
        events_to_emit.append({
            "event_type": "sale_approved",
            "signed_amount": amount,
            "competencia_date": competencia,
            "event_date": competencia,
        })

        if processor_fee > 0:
            events_to_emit.append({
                "event_type": "fee_charged",
                "signed_amount": -processor_fee,
                "competencia_date": competencia,
                "event_date": competencia,
            })

        if processor_shipping > 0:
            events_to_emit.append({
                "event_type": "shipping_charged",
                "signed_amount": -processor_shipping,
                "competencia_date": competencia,
                "event_date": competencia,
            })

    # 2. CA sync completed
    if status == "synced":
        events_to_emit.append({
            "event_type": "ca_sync_completed",
            "signed_amount": 0,
            "competencia_date": competencia,
            "event_date": competencia,
        })

    # 3. Refund events
    if status == "refunded":
        estorno_receita, date_refunded, refunded_fee, refunded_shipping = _extract_refund_info(raw)
        event_date = date_refunded or competencia

        events_to_emit.append({
            "event_type": "refund_created",
            "signed_amount": -estorno_receita,
            "competencia_date": competencia,
            "event_date": event_date,
        })

        # Estorno taxa/frete only on full refunds
        if estorno_receita >= amount:
            if refunded_fee > 0:
                events_to_emit.append({
                    "event_type": "refund_fee",
                    "signed_amount": refunded_fee,
                    "competencia_date": competencia,
                    "event_date": event_date,
                })
            if refunded_shipping > 0:
                events_to_emit.append({
                    "event_type": "refund_shipping",
                    "signed_amount": refunded_shipping,
                    "competencia_date": competencia,
                    "event_date": event_date,
                })

    # 4. Money released
    if money_release_date:
        events_to_emit.append({
            "event_type": "money_released",
            "signed_amount": 0,
            "competencia_date": money_release_date,
            "event_date": money_release_date,
        })

    # Emit all events
    for evt in events_to_emit:
        if dry_run:
            stats[f"would_emit:{evt['event_type']}"] += 1
            continue

        result = await record_event(
            seller_slug=seller_slug,
            ml_payment_id=payment_id,
            event_type=evt["event_type"],
            signed_amount=evt["signed_amount"],
            competencia_date=evt["competencia_date"],
            event_date=evt["event_date"],
            ml_order_id=order_id,
            source="backfill",
        )
        if result:
            stats[f"created:{evt['event_type']}"] += 1
        else:
            stats[f"skipped_dup:{evt['event_type']}"] += 1


async def backfill_seller(seller_slug: str, dry_run: bool = False) -> Counter:
    """Backfill all payments for a seller."""
    db = get_db()
    stats: Counter = Counter()
    offset = 0

    while True:
        result = db.table("payments").select(
            "seller_slug, ml_payment_id, status, processor_fee, processor_shipping, "
            "money_release_date, raw_payment"
        ).eq("seller_slug", seller_slug).neq(
            "status", "skipped"
        ).neq(
            "status", "skipped_non_sale"
        ).neq(
            "status", "pending"
        ).neq(
            "status", "pending_ca"
        ).order("ml_payment_id").range(offset, offset + PAGE_SIZE - 1).execute()

        rows = result.data or []
        if not rows:
            break

        for row in rows:
            await backfill_payment(row, stats, dry_run)
            stats["processed"] += 1

        offset += PAGE_SIZE
        logger.info(f"  {seller_slug}: processed {offset} rows so far...")

    return stats


async def main():
    parser = argparse.ArgumentParser(description="Backfill payment_events from payments table")
    parser.add_argument("--seller", help="Seller slug to backfill")
    parser.add_argument("--all", action="store_true", help="Backfill all sellers")
    parser.add_argument("--dry-run", action="store_true", help="Count events without writing")
    args = parser.parse_args()

    if not args.seller and not args.all:
        parser.error("Specify --seller or --all")

    db = get_db()

    if args.all:
        sellers_result = db.table("sellers").select("slug").eq("active", True).execute()
        slugs = [s["slug"] for s in (sellers_result.data or [])]
    else:
        slugs = [args.seller]

    mode = "DRY RUN" if args.dry_run else "LIVE"
    logger.info(f"=== Backfill Events ({mode}) — {len(slugs)} seller(s) ===")

    total_stats: Counter = Counter()
    for slug in slugs:
        logger.info(f"Backfilling {slug}...")
        stats = await backfill_seller(slug, args.dry_run)
        total_stats += stats
        logger.info(f"  {slug} done: {dict(stats)}")

    logger.info(f"\n=== TOTALS ===")
    for key in sorted(total_stats.keys()):
        logger.info(f"  {key}: {total_stats[key]}")


if __name__ == "__main__":
    asyncio.run(main())
