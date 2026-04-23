#!/usr/bin/env python3
"""Re-run extrato ingester for a single (seller, period). Args: seller period csv_filename."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.services.extrato_ingester import ingest_extrato_from_csv


async def main() -> int:
    if len(sys.argv) != 4:
        print("Usage: reingest_one.py <seller> <period> <csv_filename>", file=sys.stderr)
        return 2
    seller, period, fname = sys.argv[1], sys.argv[2], sys.argv[3]
    path = PROJECT_ROOT / "testes" / "data" / "extratos" / fname
    print(f"=== {seller} {period} ({fname}) ===")
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
