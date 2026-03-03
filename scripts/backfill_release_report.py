"""
Backfill release report sync for a seller.

Downloads release report(s) covering a date range and processes them through
the release_report_sync logic to capture payouts, cashback, shipping credits,
and other transactions invisible to the Payments API.

Usage: python scripts/backfill_release_report.py [seller_slug] [begin_date] [end_date]
  e.g.: python scripts/backfill_release_report.py 141air 2026-01-01 2026-03-02
"""
import asyncio
import json
import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent / ".env")


async def main():
    seller_slug = sys.argv[1] if len(sys.argv) > 1 else "141air"
    begin_date = sys.argv[2] if len(sys.argv) > 2 else "2026-01-01"
    end_date = sys.argv[3] if len(sys.argv) > 3 else "2026-03-02"

    from app.db.supabase import get_db
    from app.services.ml_api import list_release_reports, download_release_report
    from app.services.release_report_sync import (
        _parse_csv, _classify_payout, _classify_credit, _lookup_existing_ids,
        _update_existing_expense_amount,
    )
    from collections import Counter

    db = get_db()

    print(f"Backfill release report for {seller_slug}: {begin_date} to {end_date}")
    print()

    # 1. List all available reports
    reports = await list_release_reports(seller_slug, limit=100)
    print(f"Found {len(reports)} reports total")

    # 2. Filter reports that overlap with our date range
    begin_dt = datetime.strptime(begin_date, "%Y-%m-%d")
    end_dt = datetime.strptime(end_date, "%Y-%m-%d")

    matching = []
    for r in reports:
        if not isinstance(r, dict):
            continue
        r_begin = r.get("begin_date", "")
        r_end = r.get("end_date", "")
        fname = r.get("file_name", "")
        if not fname or not fname.endswith(".csv"):
            continue

        try:
            rb = datetime.fromisoformat(r_begin.replace("Z", "+00:00")).replace(tzinfo=None)
            re_ = datetime.fromisoformat(r_end.replace("Z", "+00:00")).replace(tzinfo=None)
        except (ValueError, TypeError):
            continue

        # Check overlap
        if rb <= end_dt and re_ >= begin_dt:
            matching.append({
                "file_name": fname,
                "begin": rb,
                "end": re_,
                "days": (re_ - rb).days,
            })

    # Sort by coverage (widest first) then by end date (most recent first)
    matching.sort(key=lambda x: (-x["days"], -x["end"].timestamp()))
    print(f"Reports overlapping {begin_date} to {end_date}: {len(matching)}")

    if not matching:
        print("ERROR: No reports found covering this period")
        sys.exit(1)

    # 3. Pick the fewest reports that cover the full range
    # Greedy: pick the widest report, then fill gaps
    covered_ranges: list[tuple[datetime, datetime]] = []
    selected: list[dict] = []

    for r in matching:
        # Check if this report covers any uncovered ground
        r_begin = r["begin"]
        r_end = r["end"]

        already_covered = False
        for cb, ce in covered_ranges:
            if cb <= r_begin and ce >= r_end:
                already_covered = True
                break

        if already_covered:
            continue

        selected.append(r)
        covered_ranges.append((r_begin, r_end))

        # Check if full range is covered
        merged = _merge_ranges(covered_ranges)
        if any(cb <= begin_dt and ce >= end_dt for cb, ce in merged):
            break

    print(f"\nSelected {len(selected)} report(s) to process:")
    for r in selected:
        print(f"  {r['file_name']} ({r['begin'].date()} to {r['end'].date()}, {r['days']}d)")

    # 4. Download and process each report
    all_stats = Counter()
    for r in selected:
        print(f"\n{'='*60}")
        print(f"Processing: {r['file_name']}")
        print(f"  Period: {r['begin'].date()} to {r['end'].date()}")

        content = await download_release_report(seller_slug, r["file_name"])
        if not content:
            print("  ERROR: Could not download report")
            continue

        rows = _parse_csv(content)
        print(f"  Parsed {len(rows)} relevant rows")

        if not rows:
            continue

        # Cross-reference existing IDs
        source_ids = [row["source_id"] for row in rows if row["source_id"]]
        payment_ids, expense_ids = _lookup_existing_ids(db, seller_slug, source_ids)
        print(f"  Cross-ref: {len(payment_ids)} in payments, {len(expense_ids)} in mp_expenses")

        # Process rows
        stats = Counter()
        payout_rows = [row for row in rows if row["description"] == "payout"]

        for row in rows:
            source_id = row["source_id"]
            if not source_id:
                stats["skipped_no_id"] += 1
                continue

            desc_type = row["description"]
            is_credit = row["net_credit"] > 0
            is_debit = row["net_debit"] > 0

            if desc_type == "payment":
                stats["skipped_payment"] += 1
                continue

            if desc_type in ("reserve_for_bpp_shipping_return", "reserve_for_bpp_shipping_retur"):
                if not is_credit:
                    stats["skipped_bpp_reserve"] += 1
                    continue

            if source_id in payment_ids:
                stats["already_in_payments"] += 1
                continue

            if source_id in expense_ids:
                if desc_type == "payout" and is_debit:
                    _update_existing_expense_amount(db, seller_slug, source_id, row["net_debit"])
                    stats["updated_amounts"] += 1
                else:
                    stats["already_in_expenses"] += 1
                continue

            # Classify and insert
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
                print(f"    NEW: {source_id} type={expense_type} dir={direction} amt={amount:.2f} | {description[:60]}")
            except Exception as e:
                error_str = str(e)
                if "duplicate" in error_str.lower() or "unique" in error_str.lower():
                    stats["duplicate_skipped"] += 1
                else:
                    stats["errors"] += 1
                    print(f"    ERROR: {source_id} — {e}")

        print(f"  Stats: {dict(stats)}")
        for k, v in stats.items():
            all_stats[k] += v

    # Summary
    print(f"\n{'='*60}")
    print(f"BACKFILL COMPLETE for {seller_slug}")
    print(f"  Date range: {begin_date} to {end_date}")
    print(f"  Reports processed: {len(selected)}")
    print(f"  New expenses: {all_stats.get('new_expenses', 0)}")
    print(f"  Already tracked: {all_stats.get('already_in_payments', 0) + all_stats.get('already_in_expenses', 0)}")
    print(f"  Updated amounts: {all_stats.get('updated_amounts', 0)}")
    print(f"  Errors: {all_stats.get('errors', 0)}")
    print(f"  Full breakdown: {dict(all_stats)}")


def _merge_ranges(ranges):
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


if __name__ == "__main__":
    asyncio.run(main())
