"""
Release Report Sync — parse MP release report CSV to capture transactions
invisible to the Payments API (PIX withdrawals, DARFs, ML credits/cashback).

Cross-references SOURCE_ID with payments.ml_payment_id and mp_expenses.payment_id
to avoid duplicates, then inserts new entries into mp_expenses.
"""
import csv
import io
import logging
from collections import Counter
from datetime import datetime

from app.db.supabase import get_db
from app.services.ml_api import (
    create_release_report,
    download_release_report,
    list_release_reports,
)

logger = logging.getLogger(__name__)

# Record types we care about from the release report
RELEVANT_DESCRIPTIONS = {
    "payout",
    "cashback",
    "shipping",
    "mediation_cancel",
    "reserve_for_bpp_shipping_return",
    # Truncated variant in some reports
    "reserve_for_bpp_shipping_retur",
}


def _parse_float(value: str) -> float:
    """Parse a CSV float value, handling empty strings."""
    if not value or not value.strip():
        return 0.0
    return float(value.strip())


def _parse_csv(content: bytes) -> list[dict]:
    """Parse semicolon-delimited release report CSV into list of dicts."""
    text = content.decode("utf-8-sig")  # handle BOM if present
    reader = csv.DictReader(io.StringIO(text), delimiter=";")
    rows = []
    for row in reader:
        record_type = (row.get("RECORD_TYPE") or "").strip()
        description = (row.get("DESCRIPTION") or "").strip()

        if record_type != "release":
            continue
        if description not in RELEVANT_DESCRIPTIONS:
            continue

        rows.append({
            "date": (row.get("DATE") or "").strip(),
            "source_id": (row.get("SOURCE_ID") or "").strip(),
            "external_reference": (row.get("EXTERNAL_REFERENCE") or "").strip(),
            "description": description,
            "net_credit": _parse_float(row.get("NET_CREDIT_AMOUNT", "")),
            "net_debit": _parse_float(row.get("NET_DEBIT_AMOUNT", "")),
            "gross_amount": _parse_float(row.get("GROSS_AMOUNT", "")),
            "order_id": (row.get("ORDER_ID") or "").strip(),
            "pack_id": (row.get("PACK_ID") or "").strip(),
            "payment_method": (row.get("PAYMENT_METHOD") or "").strip(),
            "payout_bank_account": (row.get("PAYOUT_BANK_ACCOUNT_NUMBER") or "").strip(),
        })
    return rows


def _classify_payout(row: dict, same_day_payouts: list[dict]) -> tuple[str, str, str]:
    """Classify a payout row into (expense_type, direction, description).

    Heuristic:
    - BOLETO in external_reference → bill_payment / expense
    - YPPROD in external_reference → bill_payment / expense (SaaS via Ypay)
    - Multiple small amounts (R$125, R$250) on same day → darf / expense
    - Large round amounts with no external_reference → transfer_pix / transfer
    - Otherwise → bill_payment / expense (pending_review)
    """
    ext_ref = row.get("external_reference", "")
    amount = row["net_debit"]

    # Explicit boleto reference
    if "BOLETO" in ext_ref.upper():
        return "bill_payment", "expense", f"Boleto (release) - {ext_ref}"

    # YPPROD = bill payment via Ypay/QR
    if ext_ref.upper().startswith("YPPROD"):
        return "bill_payment", "expense", f"Pagamento QR (release) - {ext_ref}"

    # DARF heuristic: long numeric ext_ref starting with "07" (QR Pix to government)
    if ext_ref and len(ext_ref) > 20 and ext_ref[:2] == "07" and ext_ref.isdigit():
        return "darf", "expense", f"DARF via QR Pix (release) - R$ {amount}"

    # DARF heuristic: multiple R$125 or R$250 payouts on same day
    if amount > 0:
        day = row["date"][:10]
        day_amounts = [p["net_debit"] for p in same_day_payouts if p["date"][:10] == day]
        darf_like = [a for a in day_amounts if a in (125.0, 125.00, 250.0, 250.00)]
        if len(darf_like) >= 3 and amount in (125.0, 250.0):
            return "darf", "expense", f"DARF (release, lote) - R$ {amount}"

    # PIX transfer: no external reference, typically round or large amount
    if not ext_ref:
        return "transfer_pix", "transfer", f"Saque PIX (release) - R$ {amount}"

    # Default: bill payment pending review
    return "bill_payment", "expense", f"Pagamento (release) - {ext_ref or f'R$ {amount}'}"


def _classify_credit(row: dict) -> tuple[str, str, str, str | None]:
    """Classify credit entries (cashback, shipping, mediation_cancel, bpp return).

    Returns (expense_type, direction, description, ca_category).
    """
    desc_type = row["description"]
    source_id = row["source_id"]
    ext_ref = row.get("external_reference", "")
    amount = row["net_credit"]

    if desc_type == "cashback":
        return (
            "cashback", "income",
            f"Cashback ML (release) - {ext_ref or source_id} R$ {amount}",
            "1.3.4",
        )

    if desc_type == "shipping":
        return (
            "cashback", "income",
            f"Bonus envio ML (release) - {ext_ref or source_id} R$ {amount}",
            "1.3.4",
        )

    if desc_type == "mediation_cancel":
        return (
            "cashback", "income",
            f"Estorno disputa ML (release) - {ext_ref or source_id} R$ {amount}",
            "1.3.4",
        )

    if desc_type in ("reserve_for_bpp_shipping_return", "reserve_for_bpp_shipping_retur"):
        return (
            "cashback", "income",
            f"Estorno frete BPP (release) - {ext_ref or source_id} R$ {amount}",
            "1.3.7",
        )

    return "other", "income", f"Credito ML (release) - {desc_type} R$ {amount}", None


def _lookup_existing_ids(db, seller_slug: str, source_ids: list[str]) -> tuple[set, set]:
    """Check which SOURCE_IDs already exist in payments or mp_expenses.

    Returns (payment_ids_set, expense_ids_set).
    """
    if not source_ids:
        return set(), set()

    payment_ids = set()
    expense_ids = set()

    # Check payments table (ml_payment_id)
    for i in range(0, len(source_ids), 100):
        chunk = source_ids[i:i + 100]
        int_ids = []
        for sid in chunk:
            try:
                int_ids.append(int(sid))
            except (ValueError, TypeError):
                continue
        if int_ids:
            result = db.table("payments").select("ml_payment_id").eq(
                "seller_slug", seller_slug
            ).in_("ml_payment_id", int_ids).execute()
            for r in (result.data or []):
                payment_ids.add(str(r["ml_payment_id"]))

    # Check mp_expenses table (payment_id)
    for i in range(0, len(source_ids), 100):
        chunk = source_ids[i:i + 100]
        int_ids = []
        for sid in chunk:
            try:
                int_ids.append(int(sid))
            except (ValueError, TypeError):
                continue
        if int_ids:
            result = db.table("mp_expenses").select("payment_id, amount").eq(
                "seller_slug", seller_slug
            ).in_("payment_id", int_ids).execute()
            for r in (result.data or []):
                expense_ids.add(str(r["payment_id"]))

    return payment_ids, expense_ids


def _update_existing_expense_amount(db, seller_slug: str, source_id: str, real_amount: float):
    """Update an existing mp_expense with the real amount from release report.

    This corrects IOF/exchange rate differences for SaaS/boleto payments.
    Only updates if the amount actually differs.
    """
    result = db.table("mp_expenses").select("id, amount, status").eq(
        "seller_slug", seller_slug
    ).eq("payment_id", int(source_id)).execute()

    if not result.data:
        return

    existing = result.data[0]
    if existing.get("status") == "exported":
        return  # Don't touch exported rows

    existing_amount = float(existing.get("amount") or 0)
    if abs(existing_amount - real_amount) < 0.01:
        return  # Already correct

    db.table("mp_expenses").update({
        "amount": real_amount,
        "notes": f"Amount updated from release report (was {existing_amount})",
        "updated_at": datetime.now().isoformat(),
    }).eq("id", existing["id"]).execute()

    logger.info(
        "Updated mp_expense %s amount: %.2f → %.2f (release report)",
        source_id, existing_amount, real_amount,
    )


async def sync_release_report(
    seller_slug: str,
    begin_date: str,
    end_date: str,
) -> dict:
    """Main entry point: fetch/create release report, parse, and sync to mp_expenses.

    Args:
        seller_slug: seller identifier
        begin_date: YYYY-MM-DD start date
        end_date: YYYY-MM-DD end date

    Returns dict with sync stats.
    """
    db = get_db()

    # 1. Try to find an existing report, or create a new one
    csv_content = await _get_or_create_report(seller_slug, begin_date, end_date)
    if not csv_content:
        return {"error": "Could not obtain release report", "total_rows": 0}

    # 2. Parse CSV
    rows = _parse_csv(csv_content)
    logger.info("Release report for %s: %d relevant rows parsed", seller_slug, len(rows))

    if not rows:
        return {
            "total_rows": 0,
            "new_expenses": 0,
            "already_tracked": 0,
            "updated_amounts": 0,
            "skipped": 0,
        }

    # 3. Collect all SOURCE_IDs
    source_ids = [r["source_id"] for r in rows if r["source_id"]]

    # 4. Look up which ones already exist
    payment_ids, expense_ids = _lookup_existing_ids(db, seller_slug, source_ids)
    logger.info(
        "Release report cross-ref: %d in payments, %d in mp_expenses",
        len(payment_ids), len(expense_ids),
    )

    # 5. Process each row
    stats = Counter()
    payout_rows = [r for r in rows if r["description"] == "payout"]

    for row in rows:
        source_id = row["source_id"]
        if not source_id:
            stats["skipped_no_id"] += 1
            continue

        desc_type = row["description"]
        is_credit = row["net_credit"] > 0
        is_debit = row["net_debit"] > 0

        # Skip order payments (already handled by processor.py)
        if desc_type == "payment":
            stats["skipped_payment"] += 1
            continue

        # For BPP shipping returns, only process credits (the actual return)
        if desc_type in ("reserve_for_bpp_shipping_return", "reserve_for_bpp_shipping_retur"):
            if not is_credit:
                stats["skipped_bpp_reserve"] += 1
                continue

        # Already tracked in payments table (order)
        if source_id in payment_ids:
            stats["already_in_payments"] += 1
            continue

        # Already tracked in mp_expenses
        if source_id in expense_ids:
            # Update amount if payout has different real value
            if desc_type == "payout" and is_debit:
                _update_existing_expense_amount(db, seller_slug, source_id, row["net_debit"])
                stats["updated_amounts"] += 1
            else:
                stats["already_in_expenses"] += 1
            continue

        # 6. Classify and create new mp_expense
        if desc_type == "payout":
            if not is_debit:
                stats["skipped_payout_no_debit"] += 1
                continue
            expense_type, direction, description = _classify_payout(row, payout_rows)
            amount = row["net_debit"]
            ca_category = "2.2.7" if expense_type == "darf" else None
            auto_cat = expense_type == "darf"
        else:
            if not is_credit:
                stats["skipped_credit_zero"] += 1
                continue
            expense_type, direction, description, ca_category = _classify_credit(row)
            amount = row["net_credit"]
            auto_cat = ca_category is not None

        status = "auto_categorized" if auto_cat else "pending_review"

        data = {
            "seller_slug": seller_slug,
            "payment_id": int(source_id),
            "expense_type": expense_type,
            "expense_direction": direction,
            "ca_category": ca_category,
            "auto_categorized": auto_cat,
            "amount": amount,
            "description": description[:200],
            "business_branch": None,
            "operation_type": f"release_{desc_type}",
            "payment_method": row.get("payment_method") or None,
            "external_reference": row.get("external_reference") or None,
            "febraban_code": None,
            "date_created": row["date"],
            "date_approved": row["date"],
            "status": status,
            "raw_payment": {
                "source": "release_report",
                "source_id": source_id,
                "description": desc_type,
                "net_credit": row["net_credit"],
                "net_debit": row["net_debit"],
                "gross_amount": row["gross_amount"],
                "order_id": row.get("order_id"),
                "payout_bank_account": row.get("payout_bank_account"),
            },
            "created_at": datetime.now().isoformat(),
            "updated_at": datetime.now().isoformat(),
        }

        try:
            db.table("mp_expenses").insert(data).execute()
            stats["new_expenses"] += 1
            logger.info(
                "New expense from release report: %s type=%s dir=%s amount=%.2f",
                source_id, expense_type, direction, amount,
            )
        except Exception as e:
            error_str = str(e)
            if "duplicate" in error_str.lower() or "unique" in error_str.lower():
                stats["duplicate_skipped"] += 1
                logger.debug("Duplicate mp_expense for %s, skipping", source_id)
            else:
                stats["errors"] += 1
                logger.error("Failed to insert mp_expense for %s: %s", source_id, e)

    result = {
        "total_rows": len(rows),
        "new_expenses": stats.get("new_expenses", 0),
        "already_tracked": (
            stats.get("already_in_payments", 0)
            + stats.get("already_in_expenses", 0)
        ),
        "updated_amounts": stats.get("updated_amounts", 0),
        "skipped": (
            stats.get("skipped_no_id", 0)
            + stats.get("skipped_payment", 0)
            + stats.get("skipped_bpp_reserve", 0)
            + stats.get("skipped_payout_no_debit", 0)
            + stats.get("skipped_credit_zero", 0)
        ),
        "errors": stats.get("errors", 0),
        "duplicate_skipped": stats.get("duplicate_skipped", 0),
        "breakdown": dict(stats),
    }
    logger.info("Release report sync for %s: %s", seller_slug, result)
    return result


async def _get_or_create_report(
    seller_slug: str,
    begin_date: str,
    end_date: str,
) -> bytes | None:
    """Get an existing release report or create a new one.

    Checks existing reports first; if none cover the period, requests a new one.
    """
    # List existing reports
    try:
        reports = await list_release_reports(seller_slug, limit=50)
    except Exception as e:
        logger.error("Failed to list release reports for %s: %s", seller_slug, e)
        reports = []

    # Look for a report that covers our date range
    for report in reports:
        file_name = report if isinstance(report, str) else (report.get("file_name") or "")
        if not file_name:
            continue
        # Report filenames typically contain date info; try to download the most recent
        if begin_date.replace("-", "") in file_name or end_date.replace("-", "") in file_name:
            try:
                content = await download_release_report(seller_slug, file_name)
                if content:
                    logger.info("Downloaded existing release report: %s", file_name)
                    return content
            except Exception as e:
                logger.warning("Failed to download report %s: %s", file_name, e)

    # No matching report found — create a new one
    logger.info("Creating new release report for %s: %s to %s", seller_slug, begin_date, end_date)
    try:
        result = await create_release_report(seller_slug, begin_date, end_date)
        logger.info("Release report creation result: %s", result)
    except Exception as e:
        logger.error("Failed to create release report for %s: %s", seller_slug, e)
        return None

    # The report is generated asynchronously. Try to download it.
    # Poll the list a few times with short waits.
    import asyncio
    for attempt in range(6):
        await asyncio.sleep(5)  # Wait 5s between attempts (max 30s)
        try:
            reports = await list_release_reports(seller_slug, limit=10)
        except Exception:
            continue

        for report in reports:
            file_name = report if isinstance(report, str) else (report.get("file_name") or "")
            if not file_name:
                continue
            try:
                content = await download_release_report(seller_slug, file_name)
                if content and len(content) > 100:
                    logger.info("Downloaded new release report: %s (attempt %d)", file_name, attempt + 1)
                    return content
            except Exception:
                continue

    logger.error("Release report not ready after 30s for %s", seller_slug)
    return None
