"""
Extrato Coverage Checker — verifies that ALL release report lines are covered
by API payments, mp_expenses, or legacy export.

Runs as part of the nightly pipeline after legacy export and before financial closing.
"""
import logging
from collections import Counter
from datetime import datetime, timedelta, timezone

from app.db.supabase import get_db
from app.models.sellers import get_all_active_sellers, get_seller_config
from app.services.release_report_validator import (
    _get_or_create_report,
    _parse_release_report_with_fees,
)

logger = logging.getLogger(__name__)

BRT = timezone(timedelta(hours=-3))

# In-memory last coverage result
_last_coverage_result: dict = {
    "ran_at": None,
    "results": [],
}

# Descriptions that are always covered by API processor
API_PAYMENT_DESCRIPTIONS = {"payment"}

# Descriptions that are always covered by refund logic
API_REFUND_DESCRIPTIONS = {"refund"}

# Descriptions typically covered by mp_expenses (non-order classifier or release_report_sync)
EXPENSE_DESCRIPTIONS = {
    "payout", "money_transfer", "cashback", "shipping",
    "reserve_for_bpp_shipping_return", "reserve_for_bpp_shipping_retur",
    "mediation_cancel", "reserve_for_dispute", "mediation",
    "collection", "credit_payment",
}

# Descriptions that represent internal MP accounting (can be safely skipped)
INTERNAL_DESCRIPTIONS = {
    "fund_movement", "tax_withheld_at_source",
}


def _lookup_payment_ids(db, seller_slug: str, source_ids: list[int]) -> set[int]:
    """Look up which source IDs exist in the payments table."""
    found: set[int] = set()
    for i in range(0, len(source_ids), 100):
        chunk = source_ids[i:i + 100]
        result = db.table("payments").select("ml_payment_id").eq(
            "seller_slug", seller_slug
        ).in_("ml_payment_id", chunk).execute()
        for r in (result.data or []):
            found.add(int(r["ml_payment_id"]))
    return found


def _lookup_expense_ids(db, seller_slug: str, source_ids: list[int]) -> set[int]:
    """Look up which source IDs exist in mp_expenses."""
    found: set[int] = set()
    for i in range(0, len(source_ids), 100):
        chunk = source_ids[i:i + 100]
        result = db.table("mp_expenses").select("payment_id").eq(
            "seller_slug", seller_slug
        ).in_("payment_id", chunk).execute()
        for r in (result.data or []):
            found.add(int(r["payment_id"]))
    return found


async def check_extrato_coverage(
    seller_slug: str,
    begin_date: str,
    end_date: str,
) -> dict:
    """Check that ALL release report lines are covered by payments, mp_expenses, or legacy.

    Returns coverage stats and uncovered lines.
    """
    db = get_db()
    seller = get_seller_config(db, seller_slug)
    if not seller:
        return {"seller": seller_slug, "error": "seller_not_found"}

    csv_bytes = await _get_or_create_report(seller_slug, begin_date, end_date)
    if not csv_bytes:
        return {"seller": seller_slug, "error": "report_not_available"}

    rows = _parse_release_report_with_fees(csv_bytes)

    # Only process "release" record_type rows (these are actual money movements)
    release_rows = [r for r in rows if r["record_type"] == "release"]
    logger.info("extrato_coverage %s: %d release rows from %d total", seller_slug, len(release_rows), len(rows))

    if not release_rows:
        return {
            "seller": seller_slug,
            "total_lines": 0,
            "covered_by_api": 0,
            "covered_by_expenses": 0,
            "covered_by_internal": 0,
            "uncovered": 0,
            "uncovered_lines": [],
            "coverage_pct": 100.0,
        }

    # Collect all numeric source_ids
    all_source_ids: list[int] = []
    for r in release_rows:
        try:
            all_source_ids.append(int(r["source_id"]))
        except (ValueError, TypeError):
            continue

    # Batch lookup
    payment_ids = _lookup_payment_ids(db, seller_slug, all_source_ids)
    expense_ids = _lookup_expense_ids(db, seller_slug, all_source_ids)

    stats = Counter()
    uncovered_lines: list[dict] = []

    for row in release_rows:
        source_id = row["source_id"]
        description = row["description"]

        try:
            sid_int = int(source_id) if source_id else None
        except (ValueError, TypeError):
            sid_int = None

        # 1. Payment descriptions → check payments table
        if description in API_PAYMENT_DESCRIPTIONS:
            if sid_int and sid_int in payment_ids:
                stats["covered_by_api"] += 1
                continue
            # Payment not found in our table — might be filtered (marketplace_shipment, etc.)
            if sid_int and sid_int in expense_ids:
                stats["covered_by_expenses"] += 1
                continue
            # Truly uncovered payment
            uncovered_lines.append(_uncovered_entry(row, "payment_not_tracked"))
            stats["uncovered"] += 1
            continue

        # 2. Refund descriptions → check payments table (refunded payments)
        if description in API_REFUND_DESCRIPTIONS:
            if sid_int and sid_int in payment_ids:
                stats["covered_by_api"] += 1
                continue
            if sid_int and sid_int in expense_ids:
                stats["covered_by_expenses"] += 1
                continue
            uncovered_lines.append(_uncovered_entry(row, "refund_not_tracked"))
            stats["uncovered"] += 1
            continue

        # 3. Expense descriptions → check mp_expenses
        if description in EXPENSE_DESCRIPTIONS:
            if sid_int and sid_int in expense_ids:
                stats["covered_by_expenses"] += 1
                continue
            if sid_int and sid_int in payment_ids:
                stats["covered_by_api"] += 1
                continue
            uncovered_lines.append(_uncovered_entry(row, "expense_not_tracked"))
            stats["uncovered"] += 1
            continue

        # 4. Internal descriptions → always covered
        if description in INTERNAL_DESCRIPTIONS:
            stats["covered_by_internal"] += 1
            continue

        # 5. Unknown description → check both tables
        if sid_int and (sid_int in payment_ids or sid_int in expense_ids):
            stats["covered_by_api" if sid_int in payment_ids else "covered_by_expenses"] += 1
            continue

        uncovered_lines.append(_uncovered_entry(row, f"unknown_description:{description}"))
        stats["uncovered"] += 1

    total = len(release_rows)
    covered = total - stats.get("uncovered", 0)
    coverage_pct = round((covered / total * 100) if total > 0 else 100.0, 2)

    result = {
        "seller": seller_slug,
        "total_lines": total,
        "covered_by_api": stats.get("covered_by_api", 0),
        "covered_by_expenses": stats.get("covered_by_expenses", 0),
        "covered_by_internal": stats.get("covered_by_internal", 0),
        "uncovered": stats.get("uncovered", 0),
        "uncovered_lines": uncovered_lines[:100],  # Limit to 100 for response size
        "coverage_pct": coverage_pct,
        "breakdown": dict(stats),
    }
    logger.info("extrato_coverage %s: %s", seller_slug, {k: v for k, v in result.items() if k != "uncovered_lines"})
    return result


def _uncovered_entry(row: dict, reason: str) -> dict:
    """Build a summary dict for an uncovered release report line."""
    net = row["net_credit_amount"] - row["net_debit_amount"]
    return {
        "source_id": row["source_id"],
        "description": row["description"],
        "date": row["date"][:10] if row.get("date") else None,
        "net_amount": round(net, 2),
        "gross_amount": row.get("gross_amount", 0),
        "external_reference": row.get("external_reference"),
        "reason": reason,
    }


# ---------------------------------------------------------------------------
# All-sellers entry point
# ---------------------------------------------------------------------------

async def check_extrato_coverage_all_sellers(lookback_days: int = 3) -> list[dict]:
    """Coverage check for all active sellers (D-1 to D-{lookback_days})."""
    db = get_db()
    sellers = get_all_active_sellers(db)

    now_brt = datetime.now(BRT)
    end_date = (now_brt - timedelta(days=1)).strftime("%Y-%m-%d")
    begin_date = (now_brt - timedelta(days=lookback_days)).strftime("%Y-%m-%d")

    results = []
    for seller in sellers:
        slug = seller["slug"]
        try:
            result = await check_extrato_coverage(slug, begin_date, end_date)
            results.append(result)
        except Exception as e:
            logger.error("extrato_coverage error for %s: %s", slug, e, exc_info=True)
            results.append({"seller": slug, "error": str(e)})

    _last_coverage_result["ran_at"] = datetime.now(timezone.utc).isoformat()
    _last_coverage_result["results"] = results
    return results


def get_last_coverage_result() -> dict:
    return _last_coverage_result
