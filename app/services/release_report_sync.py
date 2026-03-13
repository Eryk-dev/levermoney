"""
Release Report Sync — parse MP release report CSV to capture transactions
invisible to the Payments API (PIX withdrawals, DARFs, ML credits/cashback).

Cross-references SOURCE_ID with payment_events to avoid duplicates,
then records new entries via the event ledger.
"""
import csv
import io
import logging
from collections import Counter
from datetime import datetime, timedelta, timezone

from app.db.supabase import get_db
from app.models.sellers import get_all_active_sellers
from app.services.ml_api import (
    create_release_report,
    download_release_report,
    list_release_reports,
)
from app.services.event_ledger import (
    EventRecordError,
    record_expense_event,
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
        bank_acc = row.get("payout_bank_account", "")
        suffix = f" p/ conta {bank_acc}" if bank_acc else ""
        return "transfer_pix", "transfer", f"Saque PIX{suffix} - R$ {amount}"

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
            "1.3.4 Descontos e Estornos de Taxas e Tarifas",
        )

    if desc_type == "shipping":
        return (
            "cashback", "income",
            f"Bonus envio ML (release) - {ext_ref or source_id} R$ {amount}",
            "1.3.4 Descontos e Estornos de Taxas e Tarifas",
        )

    if desc_type == "mediation_cancel":
        return (
            "cashback", "income",
            f"Estorno disputa ML (release) - {ext_ref or source_id} R$ {amount}",
            "1.3.4 Descontos e Estornos de Taxas e Tarifas",
        )

    if desc_type in ("reserve_for_bpp_shipping_return", "reserve_for_bpp_shipping_retur"):
        return (
            "cashback", "income",
            f"Estorno frete BPP (release) - {ext_ref or source_id} R$ {amount}",
            "1.3.7 Estorno de Frete sobre Vendas",
        )

    return "other", "income", f"Credito ML (release) - {desc_type} R$ {amount}", None


def _release_signed_amount(direction: str, amount: float) -> float:
    """Return signed amount: positive for income, negative for expense/transfer."""
    if direction == "income":
        return abs(amount)
    return -abs(amount)


def _build_release_expense_metadata(
    row: dict,
    expense_type: str,
    direction: str,
    ca_category: str | None,
    auto_cat: bool,
    description: str,
    amount: float,
) -> dict:
    """Build rich metadata dict for expense_captured event from release report row."""
    return {
        "expense_type": expense_type,
        "expense_direction": direction,
        "ca_category": ca_category,
        "auto_categorized": auto_cat,
        "description": description,
        "amount": amount,
        "date_created": row["date"],
        "date_approved": row["date"],
        "business_branch": None,
        "operation_type": f"release_{row['description']}",
        "payment_method": row.get("payment_method") or None,
        "external_reference": row.get("external_reference") or None,
        "beneficiary_name": None,
        "notes": row.get("payout_bank_account") or None,
    }


async def _write_release_expense_events(
    seller_slug: str,
    source_id: str,
    expense_type: str,
    direction: str,
    ca_category: str | None,
    auto_cat: bool,
    row: dict,
    description: str,
    amount: float,
) -> None:
    """Write expense_captured (and expense_classified if auto) to event ledger.

    Failures are logged as warnings but do not propagate.
    """
    signed = _release_signed_amount(direction, amount)
    competencia = row["date"][:10]
    metadata = _build_release_expense_metadata(
        row, expense_type, direction, ca_category, auto_cat, description, amount,
    )

    try:
        await record_expense_event(
            seller_slug=seller_slug,
            payment_id=source_id,
            event_type="expense_captured",
            signed_amount=signed,
            competencia_date=competencia,
            expense_type=expense_type,
            metadata=metadata,
        )
    except EventRecordError:
        logger.warning(
            "expense_captured failed for %s/%s, continuing",
            seller_slug, source_id,
        )

    if auto_cat:
        try:
            await record_expense_event(
                seller_slug=seller_slug,
                payment_id=source_id,
                event_type="expense_classified",
                signed_amount=0,
                competencia_date=competencia,
                expense_type=expense_type,
                metadata={"ca_category": ca_category},
            )
        except EventRecordError:
            logger.warning(
                "expense_classified failed for %s/%s, continuing",
                seller_slug, source_id,
            )


async def _lookup_existing_ids(db, seller_slug: str, source_ids: list[str]) -> tuple[set, set]:
    """Check which SOURCE_IDs already exist in payment_events.

    Returns (payment_ids_set, expense_ids_set).
    payment_ids_set: IDs with sale_approved events (order payments).
    expense_ids_set: IDs with expense_captured events (non-order expenses).
    """
    if not source_ids:
        return set(), set()

    from app.services import event_ledger

    # Check for order-level payment events (sale_approved etc.)
    int_ids = []
    for sid in source_ids:
        try:
            int_ids.append(int(sid))
        except (ValueError, TypeError):
            continue

    found_ints = await event_ledger.get_processed_payment_ids_in(seller_slug, int_ids)
    payment_ids = {str(pid) for pid in found_ints}

    # Check for expense_captured events in payment_events
    expense_ids: set[str] = set()
    if int_ids:
        for i in range(0, len(int_ids), 100):
            chunk = int_ids[i:i + 100]
            result = db.table("payment_events").select("ml_payment_id").eq(
                "seller_slug", seller_slug
            ).eq("event_type", "expense_captured").in_(
                "ml_payment_id", chunk
            ).execute()
            for r in (result.data or []):
                expense_ids.add(str(r["ml_payment_id"]))

    return payment_ids, expense_ids



async def sync_release_report(
    seller_slug: str,
    begin_date: str,
    end_date: str,
) -> dict:
    """Main entry point: fetch/create release report, parse, and sync to event ledger.

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
    payment_ids, expense_ids = await _lookup_existing_ids(db, seller_slug, source_ids)
    logger.info(
        "Release report cross-ref: %d in payments, %d in expenses",
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

        # Already tracked in payment_events (order)
        if source_id in payment_ids:
            stats["already_in_payments"] += 1
            continue

        # Already tracked as expense in event ledger
        if source_id in expense_ids:
            stats["already_in_expenses"] += 1
            continue

        # 6. Classify and record via event ledger
        if desc_type == "payout":
            if not is_debit:
                stats["skipped_payout_no_debit"] += 1
                continue
            expense_type, direction, description = _classify_payout(row, payout_rows)
            amount = row["net_debit"]
            ca_category = "2.2.7 Simples Nacional" if expense_type == "darf" else None
            auto_cat = expense_type == "darf"
        else:
            if not is_credit:
                stats["skipped_credit_zero"] += 1
                continue
            expense_type, direction, description, ca_category = _classify_credit(row)
            amount = row["net_credit"]
            auto_cat = ca_category is not None

        await _write_release_expense_events(
            seller_slug, source_id, expense_type, direction,
            ca_category, auto_cat, row, description, amount,
        )
        stats["new_expenses"] += 1
        logger.info(
            "New expense from release report: %s type=%s dir=%s amount=%.2f",
            source_id, expense_type, direction, amount,
        )

    result = {
        "total_rows": len(rows),
        "new_expenses": stats.get("new_expenses", 0),
        "already_tracked": (
            stats.get("already_in_payments", 0)
            + stats.get("already_in_expenses", 0)
        ),
        "skipped": (
            stats.get("skipped_no_id", 0)
            + stats.get("skipped_payment", 0)
            + stats.get("skipped_bpp_reserve", 0)
            + stats.get("skipped_payout_no_debit", 0)
            + stats.get("skipped_credit_zero", 0)
        ),
        "errors": stats.get("errors", 0),
        "breakdown": dict(stats),
    }
    logger.info("Release report sync for %s: %s", seller_slug, result)
    return result


BRT = timezone(timedelta(hours=-3))


async def sync_release_report_all_sellers(lookback_days: int = 3) -> list[dict]:
    """Run release report sync for all active sellers (D-1 to D-{lookback_days}).

    Entry point for the nightly pipeline. Captures payouts, cashback, shipping
    credits, and other transactions invisible to the Payments API.
    """
    db = get_db()
    sellers = get_all_active_sellers(db)

    now_brt = datetime.now(BRT)
    end_date = (now_brt - timedelta(days=1)).strftime("%Y-%m-%d")
    begin_date = (now_brt - timedelta(days=lookback_days)).strftime("%Y-%m-%d")

    logger.info(
        "release_report_sync: starting for %d sellers, window %s → %s",
        len(sellers), begin_date, end_date,
    )

    results: list[dict] = []
    for seller in sellers:
        slug = seller["slug"]
        try:
            result = await sync_release_report(slug, begin_date, end_date)
            result["seller"] = slug
            results.append(result)
        except Exception as exc:
            logger.error(
                "release_report_sync: error for %s — %s", slug, exc, exc_info=True,
            )
            results.append({"seller": slug, "error": str(exc)})

    total_new = sum(r.get("new_expenses", 0) for r in results)
    logger.info("release_report_sync: completed. total_new=%d", total_new)
    return results


async def backfill_release_report(
    seller_slug: str,
    begin_date: str,
    end_date: str,
) -> dict:
    """Backfill release report sync for a large date range.

    Unlike sync_release_report (which uses a single report), this function
    downloads ALL available reports overlapping the date range and processes
    each one. This handles historical backfills where no single report covers
    the entire period.

    Idempotent: duplicate SOURCE_IDs are safely skipped.

    Args:
        seller_slug: seller identifier
        begin_date: YYYY-MM-DD start date
        end_date: YYYY-MM-DD end date

    Returns dict with aggregate sync stats.
    """
    import asyncio as _asyncio

    db = get_db()
    begin_dt = datetime.strptime(begin_date, "%Y-%m-%d")
    end_dt = datetime.strptime(end_date, "%Y-%m-%d")

    # 1. List all available reports
    try:
        reports = await list_release_reports(seller_slug, limit=100)
    except Exception as e:
        logger.error("backfill_release_report %s: failed to list reports: %s", seller_slug, e)
        return {"seller": seller_slug, "error": f"list_reports_failed: {e}"}

    # 2. Filter reports that overlap with our date range and are CSVs
    matching: list[dict] = []
    for r in reports:
        if not isinstance(r, dict):
            continue
        fname = r.get("file_name", "")
        if not fname or not fname.endswith(".csv"):
            continue
        try:
            rb = datetime.fromisoformat(
                (r.get("begin_date") or "").replace("Z", "+00:00")
            ).replace(tzinfo=None)
            re = datetime.fromisoformat(
                (r.get("end_date") or "").replace("Z", "+00:00")
            ).replace(tzinfo=None)
        except (ValueError, TypeError):
            continue
        if rb <= end_dt and re >= begin_dt:
            matching.append({"file_name": fname, "begin": rb, "end": re, "days": (re - rb).days})

    # Sort widest first, then most recent
    matching.sort(key=lambda x: (-x["days"], -x["end"].timestamp()))

    # 3. Greedy selection: pick fewest reports to cover the range
    covered: list[tuple[datetime, datetime]] = []
    selected: list[dict] = []
    for r in matching:
        already = any(cb <= r["begin"] and ce >= r["end"] for cb, ce in covered)
        if already:
            continue
        selected.append(r)
        covered.append((r["begin"], r["end"]))
        merged = _merge_ranges(covered)
        if any(cb <= begin_dt and ce >= end_dt for cb, ce in merged):
            break

    if not selected:
        logger.warning("backfill_release_report %s: no reports found for %s to %s", seller_slug, begin_date, end_date)
        return {"seller": seller_slug, "error": "no_reports_found", "new_expenses": 0}

    logger.info(
        "backfill_release_report %s: processing %d report(s) for %s to %s",
        seller_slug, len(selected), begin_date, end_date,
    )

    # 4. Download and process each report
    total_stats = Counter()
    for r in selected:
        try:
            content = await download_release_report(seller_slug, r["file_name"])
        except Exception as e:
            logger.error("backfill_release_report %s: download failed %s: %s", seller_slug, r["file_name"], e)
            total_stats["download_errors"] += 1
            continue

        if not content:
            total_stats["download_errors"] += 1
            continue

        rows = _parse_csv(content)
        if not rows:
            continue

        source_ids = [row["source_id"] for row in rows if row["source_id"]]
        payment_ids, expense_ids = await _lookup_existing_ids(db, seller_slug, source_ids)

        payout_rows = [row for row in rows if row["description"] == "payout"]

        for row in rows:
            source_id = row["source_id"]
            if not source_id:
                total_stats["skipped_no_id"] += 1
                continue

            desc_type = row["description"]
            is_credit = row["net_credit"] > 0
            is_debit = row["net_debit"] > 0

            if desc_type == "payment":
                total_stats["skipped_payment"] += 1
                continue

            if desc_type in ("reserve_for_bpp_shipping_return", "reserve_for_bpp_shipping_retur"):
                if not is_credit:
                    total_stats["skipped_bpp_reserve"] += 1
                    continue

            if source_id in payment_ids:
                total_stats["already_in_payments"] += 1
                continue

            if source_id in expense_ids:
                total_stats["already_in_expenses"] += 1
                continue

            if desc_type == "payout":
                if not is_debit:
                    total_stats["skipped_payout_no_debit"] += 1
                    continue
                expense_type, direction, description = _classify_payout(row, payout_rows)
                amount = row["net_debit"]
                ca_category = "2.2.7 Simples Nacional" if expense_type == "darf" else None
                auto_cat = expense_type == "darf"
            else:
                if not is_credit:
                    total_stats["skipped_credit_zero"] += 1
                    continue
                expense_type, direction, description, ca_category = _classify_credit(row)
                amount = row["net_credit"]
                auto_cat = ca_category is not None

            await _write_release_expense_events(
                seller_slug, source_id, expense_type, direction,
                ca_category, auto_cat, row, description, amount,
            )
            total_stats["new_expenses"] += 1
            logger.info(
                "backfill_release_report %s: new %s type=%s dir=%s amount=%.2f",
                seller_slug, source_id, expense_type, direction, amount,
            )

        # Small delay between reports
        await _asyncio.sleep(1)

    result = {
        "seller": seller_slug,
        "period": f"{begin_date} to {end_date}",
        "reports_processed": len(selected),
        "new_expenses": total_stats.get("new_expenses", 0),
        "already_tracked": (
            total_stats.get("already_in_payments", 0)
            + total_stats.get("already_in_expenses", 0)
        ),
        "errors": total_stats.get("errors", 0),
        "breakdown": dict(total_stats),
    }
    logger.info("backfill_release_report %s: %s", seller_slug, result)
    return result


def _merge_ranges(
    ranges: list[tuple[datetime, datetime]],
) -> list[tuple[datetime, datetime]]:
    """Merge overlapping datetime ranges."""
    if not ranges:
        return []
    sorted_ranges = sorted(ranges, key=lambda x: x[0])
    merged = [sorted_ranges[0]]
    for start, end in sorted_ranges[1:]:
        if start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


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
