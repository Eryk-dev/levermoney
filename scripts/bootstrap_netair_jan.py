#!/usr/bin/env python3
"""Bootstrap net-air January 2026 data for reconciliation test.

Steps:
  1. sync_seller_payments('net-air', '2026-01-01', '2026-01-31')
  2. sync_release_report('net-air', '2026-01-01', '2026-01-31')
  3. ingest_extrato_from_csv('net-air', <CSV>, '2026-01')
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.services.daily_sync import sync_seller_payments
from app.services.release_report_sync import sync_release_report
from app.services.extrato_ingester import ingest_extrato_from_csv


SELLER = "net-air"
BEGIN = "2026-01-01"
END = "2026-01-31"
MONTH = "2026-01"
CSV_PATH = PROJECT_ROOT / "testes" / "data" / "extratos" / "extrato janeiro netair.csv"


async def step1_sync_payments() -> None:
    print(f"[1/3] sync_seller_payments({SELLER!r}, {BEGIN!r}, {END!r})")
    res = await sync_seller_payments(SELLER, BEGIN, END)
    for k in ("total_payments", "orders_processed", "orders_errors",
              "non_orders_classified", "non_orders_errors", "skipped"):
        if k in res:
            print(f"    {k}: {res[k]}")


async def step2_sync_release_report() -> None:
    print(f"[2/3] sync_release_report({SELLER!r}, {BEGIN!r}, {END!r})")
    res = await sync_release_report(SELLER, BEGIN, END)
    for k, v in res.items():
        print(f"    {k}: {v}")


async def step3_ingest_extrato() -> None:
    print(f"[3/3] ingest_extrato_from_csv({SELLER!r}, ..., {MONTH!r}) path={CSV_PATH.name}")
    csv_text = CSV_PATH.read_text(encoding="utf-8-sig")
    res = await ingest_extrato_from_csv(SELLER, csv_text, MONTH)
    for k, v in res.items():
        if k in ("summary", "by_type"):
            continue
        print(f"    {k}: {v}")
    print(f"    by_type: {res.get('by_type')}")


async def main() -> int:
    await step1_sync_payments()
    await step2_sync_release_report()
    await step3_ingest_extrato()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
