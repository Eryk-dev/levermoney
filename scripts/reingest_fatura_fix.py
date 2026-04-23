#!/usr/bin/env python3
"""Re-run extrato ingester on easy-utilidades jan + netparts jan/feb after ERR-0033 fix.

The fuzzy-match namespace guard now preserves distinct faturas with matching
amounts when both refs live in the same ID namespace. Run to backfill the
lines that were previously skipped.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.services.extrato_ingester import ingest_extrato_from_csv

JOBS = [
    ("easy-utilidades", "2026-01", "extrato janeiro Easyutilidades.csv"),
    ("netparts-sp", "2026-01", "extrato janeiro netparts.csv"),
    ("netparts-sp", "2026-02", "extrato fevereiro netparts.csv"),
]


async def main() -> int:
    extratos_dir = PROJECT_ROOT / "testes" / "data" / "extratos"
    for seller, period, fname in JOBS:
        path = extratos_dir / fname
        print(f"\n=== {seller} {period} ({fname}) ===")
        csv_text = path.read_text(encoding="utf-8-sig")
        res = await ingest_extrato_from_csv(seller, csv_text, period)
        for k, v in res.items():
            if k == "by_type":
                continue
            print(f"    {k}: {v}")
        print(f"    by_type: {res.get('by_type')}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
