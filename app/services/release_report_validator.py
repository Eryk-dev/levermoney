"""
Release Report Fee Validator — validates processor fees against release report
MP_FEE_AMOUNT and SHIPPING_FEE_AMOUNT, creating CA adjustment jobs when needed.

Runs as part of the nightly pipeline after sync and before baixas.
"""
import asyncio
import csv
import io
import logging
from collections import Counter
from datetime import datetime, timedelta, timezone

from app.db.supabase import get_db
from app.models.sellers import CA_CATEGORIES, get_all_active_sellers, get_seller_config
from app.services import ca_queue, ml_api
from app.services.ca_api import CA_API
from app.services.processor import _build_despesa_payload

logger = logging.getLogger(__name__)

BRT = timezone(timedelta(hours=-3))

# Polling config for report readiness
POLL_INTERVAL_SECONDS = 10
MAX_POLL_SECONDS = 300

# In-memory last validation result
_last_validation_result: dict = {
    "ran_at": None,
    "results": [],
}


# ---------------------------------------------------------------------------
# CSV parsing
# ---------------------------------------------------------------------------

def _parse_float(value: str) -> float:
    if not value or not value.strip():
        return 0.0
    try:
        return float(value.strip())
    except (ValueError, TypeError):
        return 0.0


def _parse_release_report_with_fees(csv_bytes: bytes) -> list[dict]:
    """Parse release report CSV including fee breakdown columns.

    Returns list of dicts with standardised keys for all rows (not just payments).
    """
    text = csv_bytes.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text), delimiter=";")
    rows = []
    for row in reader:
        rows.append({
            "date": (row.get("DATE") or "").strip(),
            "source_id": (row.get("SOURCE_ID") or "").strip(),
            "external_reference": (row.get("EXTERNAL_REFERENCE") or "").strip(),
            "record_type": (row.get("RECORD_TYPE") or "").strip(),
            "description": (row.get("DESCRIPTION") or "").strip(),
            "net_credit_amount": _parse_float(row.get("NET_CREDIT_AMOUNT", "")),
            "net_debit_amount": _parse_float(row.get("NET_DEBIT_AMOUNT", "")),
            "gross_amount": _parse_float(row.get("GROSS_AMOUNT", "")),
            "mp_fee_amount": _parse_float(row.get("MP_FEE_AMOUNT", "")),
            "financing_fee_amount": _parse_float(row.get("FINANCING_FEE_AMOUNT", "")),
            "shipping_fee_amount": _parse_float(row.get("SHIPPING_FEE_AMOUNT", "")),
            "taxes_amount": _parse_float(row.get("TAXES_AMOUNT", "")),
            "coupon_amount": _parse_float(row.get("COUPON_AMOUNT", "")),
            "order_id": (row.get("ORDER_ID") or "").strip(),
            "payment_method": (row.get("PAYMENT_METHOD") or "").strip(),
            "approval_date": (row.get("TRANSACTION_APPROVAL_DATE") or "").strip(),
        })
    return rows


# ---------------------------------------------------------------------------
# Report fetching (reuses ml_api + polling pattern from legacy_daily_export)
# ---------------------------------------------------------------------------

async def _get_or_create_report(seller_slug: str, begin_date: str, end_date: str) -> bytes | None:
    """Get/create a release report and return raw CSV bytes, or None on failure."""
    # Try to find existing report
    try:
        reports = await ml_api.list_release_reports(seller_slug, limit=50)
    except Exception as e:
        logger.warning("release_report_validator %s: list_reports failed: %s", seller_slug, e)
        reports = []

    for report in reports:
        file_name = report if isinstance(report, str) else (report.get("file_name") or "")
        if not file_name:
            continue
        if begin_date.replace("-", "") in file_name or end_date.replace("-", "") in file_name:
            try:
                content = await ml_api.download_release_report(seller_slug, file_name)
                if content and len(content) > 100:
                    return content
            except Exception:
                continue

    # Create new report
    try:
        await ml_api.create_release_report(seller_slug, begin_date, end_date)
    except Exception as e:
        logger.error("release_report_validator %s: create_report failed: %s", seller_slug, e)
        return None

    # Poll until ready
    elapsed = 0
    while elapsed < MAX_POLL_SECONDS:
        await asyncio.sleep(POLL_INTERVAL_SECONDS)
        elapsed += POLL_INTERVAL_SECONDS
        try:
            reports = await ml_api.list_release_reports(seller_slug, limit=20)
        except Exception:
            continue
        for report in reports:
            file_name = report if isinstance(report, str) else (report.get("file_name") or "")
            if not file_name:
                continue
            try:
                content = await ml_api.download_release_report(seller_slug, file_name)
                if content and len(content) > 100:
                    return content
            except Exception:
                continue

    logger.error("release_report_validator %s: report not ready after %ds", seller_slug, MAX_POLL_SECONDS)
    return None


# ---------------------------------------------------------------------------
# Core validation logic
# ---------------------------------------------------------------------------

async def validate_release_fees_for_seller(
    seller_slug: str,
    begin_date: str,
    end_date: str,
) -> dict:
    """Validate processor fees against release report for a seller.

    1. Download/create release report
    2. Parse fee columns
    3. Compare with processor_fee/processor_shipping in payments table
    4. Create CA adjustment jobs for differences
    5. Save data in release_report_fees table

    Returns stats dict.
    """
    db = get_db()
    seller = get_seller_config(db, seller_slug)
    if not seller:
        return {"seller": seller_slug, "error": "seller_not_found"}

    csv_bytes = await _get_or_create_report(seller_slug, begin_date, end_date)
    if not csv_bytes:
        return {"seller": seller_slug, "error": "report_not_available"}

    rows = _parse_release_report_with_fees(csv_bytes)
    logger.info("release_report_validator %s: parsed %d rows", seller_slug, len(rows))

    # Filter to payment rows with release record_type
    payment_rows = [
        r for r in rows
        if r["record_type"] == "release" and r["description"] == "payment"
    ]

    if not payment_rows:
        return {
            "seller": seller_slug,
            "total_rows": len(rows),
            "payment_rows": 0,
            "adjustments_created": 0,
            "already_adjusted": 0,
        }

    # Batch-load processor fees from payments table
    source_ids = [r["source_id"] for r in payment_rows if r["source_id"]]
    int_ids = []
    for sid in source_ids:
        try:
            int_ids.append(int(sid))
        except (ValueError, TypeError):
            continue

    # Load in chunks of 100
    payments_by_id: dict[int, dict] = {}
    for i in range(0, len(int_ids), 100):
        chunk = int_ids[i:i + 100]
        result = db.table("payments").select(
            "ml_payment_id, processor_fee, processor_shipping, fee_adjusted, amount, "
            "money_release_date, seller_slug"
        ).eq("seller_slug", seller_slug).in_("ml_payment_id", chunk).execute()
        for p in (result.data or []):
            payments_by_id[int(p["ml_payment_id"])] = p

    stats = Counter()

    for row in payment_rows:
        source_id = row["source_id"]
        if not source_id:
            stats["skipped_no_id"] += 1
            continue

        try:
            pid = int(source_id)
        except (ValueError, TypeError):
            stats["skipped_invalid_id"] += 1
            continue

        # Save to release_report_fees for audit trail
        _save_release_report_fee(db, seller_slug, row)

        payment = payments_by_id.get(pid)
        if not payment:
            stats["not_in_payments"] += 1
            continue

        if payment.get("fee_adjusted"):
            stats["already_adjusted"] += 1
            continue

        processor_fee = float(payment.get("processor_fee") or 0)
        processor_shipping = float(payment.get("processor_shipping") or 0)

        # Release report fees are typically negative (deductions), take absolute value
        release_fee = abs(row["mp_fee_amount"])
        release_shipping = abs(row["shipping_fee_amount"])

        fee_diff = round(release_fee - processor_fee, 2)
        shipping_diff = round(release_shipping - processor_shipping, 2)

        adjustments_made = 0

        # Fee adjustment (hidden commission)
        if fee_diff >= 0.01:
            competencia = (row.get("approval_date") or row["date"])[:10]
            release_date = row["date"][:10]
            if not competencia or competencia == "":
                competencia = release_date

            ajuste_payload = _build_despesa_payload(
                seller, competencia, release_date, fee_diff,
                f"Ajuste Comissão ML - Payment {pid}",
                f"charges_details={processor_fee}, release_report={release_fee}, diff={fee_diff}",
                CA_CATEGORIES["comissao_ml"],
            )
            await ca_queue.enqueue(
                seller_slug=seller_slug,
                job_type="ajuste_comissao",
                ca_endpoint=f"{CA_API}/v1/financeiro/eventos-financeiros/contas-a-pagar",
                ca_payload=ajuste_payload,
                idempotency_key=f"{seller_slug}:{pid}:ajuste_fee",
                group_id=f"{seller_slug}:{pid}:ajustes",
                priority=25,
            )
            adjustments_made += 1
            stats["fee_adjustments"] += 1
            logger.info(
                "Payment %s: fee adjustment R$%.2f (processor=%.2f, release=%.2f)",
                pid, fee_diff, processor_fee, release_fee,
            )

        # Shipping adjustment (hidden shipping fee)
        if shipping_diff >= 0.01:
            competencia = (row.get("approval_date") or row["date"])[:10]
            release_date = row["date"][:10]
            if not competencia or competencia == "":
                competencia = release_date

            ajuste_shipping_payload = _build_despesa_payload(
                seller, competencia, release_date, shipping_diff,
                f"Ajuste Frete ML - Payment {pid}",
                f"charges_shipping={processor_shipping}, release_report={release_shipping}, diff={shipping_diff}",
                CA_CATEGORIES["frete_mercadoenvios"],
            )
            await ca_queue.enqueue(
                seller_slug=seller_slug,
                job_type="ajuste_frete",
                ca_endpoint=f"{CA_API}/v1/financeiro/eventos-financeiros/contas-a-pagar",
                ca_payload=ajuste_shipping_payload,
                idempotency_key=f"{seller_slug}:{pid}:ajuste_shipping",
                group_id=f"{seller_slug}:{pid}:ajustes",
                priority=25,
            )
            adjustments_made += 1
            stats["shipping_adjustments"] += 1
            logger.info(
                "Payment %s: shipping adjustment R$%.2f (processor=%.2f, release=%.2f)",
                pid, shipping_diff, processor_shipping, release_shipping,
            )

        # Negative diff (release < processor) — ML charged less than expected
        if fee_diff <= -0.01:
            stats["fee_overcharged"] += 1
            logger.info(
                "Payment %s: processor fee higher than release (processor=%.2f, release=%.2f, diff=%.2f)",
                pid, processor_fee, release_fee, fee_diff,
            )

        if shipping_diff <= -0.01:
            stats["shipping_overcharged"] += 1

        # Mark payment as fee_adjusted if any adjustment was made
        if adjustments_made > 0:
            db.table("payments").update({
                "fee_adjusted": True,
                "updated_at": datetime.now().isoformat(),
            }).eq("ml_payment_id", pid).eq("seller_slug", seller_slug).execute()
            stats["payments_adjusted"] += 1
        else:
            stats["no_diff"] += 1

    result = {
        "seller": seller_slug,
        "total_rows": len(rows),
        "payment_rows": len(payment_rows),
        "adjustments_created": stats.get("fee_adjustments", 0) + stats.get("shipping_adjustments", 0),
        "payments_adjusted": stats.get("payments_adjusted", 0),
        "already_adjusted": stats.get("already_adjusted", 0),
        "not_in_payments": stats.get("not_in_payments", 0),
        "no_diff": stats.get("no_diff", 0),
        "fee_overcharged": stats.get("fee_overcharged", 0),
        "breakdown": dict(stats),
    }
    logger.info("release_report_validator %s: %s", seller_slug, result)
    return result


def _save_release_report_fee(db, seller_slug: str, row: dict):
    """Save a parsed release report row to release_report_fees (upsert)."""
    data = {
        "seller_slug": seller_slug,
        "source_id": row["source_id"],
        "release_date": row["date"][:10] if row.get("date") else None,
        "description": row.get("description"),
        "record_type": row.get("record_type"),
        "gross_amount": row.get("gross_amount"),
        "mp_fee_amount": row.get("mp_fee_amount"),
        "financing_fee_amount": row.get("financing_fee_amount"),
        "shipping_fee_amount": row.get("shipping_fee_amount"),
        "taxes_amount": row.get("taxes_amount"),
        "coupon_amount": row.get("coupon_amount"),
        "net_credit_amount": row.get("net_credit_amount"),
        "net_debit_amount": row.get("net_debit_amount"),
        "external_reference": row.get("external_reference") or None,
        "order_id": row.get("order_id") or None,
        "payment_method": row.get("payment_method") or None,
    }
    try:
        db.table("release_report_fees").upsert(
            data,
            on_conflict="seller_slug,source_id,release_date,description",
        ).execute()
    except Exception as e:
        logger.debug("release_report_fees upsert error for %s/%s: %s", seller_slug, row["source_id"], e)


# ---------------------------------------------------------------------------
# All-sellers entry point
# ---------------------------------------------------------------------------

async def validate_release_fees_all_sellers(lookback_days: int = 3) -> list[dict]:
    """Validate fees for all active sellers (D-1 to D-{lookback_days})."""
    db = get_db()
    sellers = get_all_active_sellers(db)

    now_brt = datetime.now(BRT)
    end_date = (now_brt - timedelta(days=1)).strftime("%Y-%m-%d")
    begin_date = (now_brt - timedelta(days=lookback_days)).strftime("%Y-%m-%d")

    results = []
    for seller in sellers:
        slug = seller["slug"]
        try:
            result = await validate_release_fees_for_seller(slug, begin_date, end_date)
            results.append(result)
        except Exception as e:
            logger.error("release_report_validator error for %s: %s", slug, e, exc_info=True)
            results.append({"seller": slug, "error": str(e)})

    _last_validation_result["ran_at"] = datetime.now(timezone.utc).isoformat()
    _last_validation_result["results"] = results
    return results


def get_last_validation_result() -> dict:
    return _last_validation_result
