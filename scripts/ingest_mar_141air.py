#!/usr/bin/env python3
"""Ingest the March 2026 141air extrato CSV."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.services.extrato_ingester import ingest_extrato_from_csv


async def main() -> int:
    seller = "141air"
    month = "2026-03"
    csv_path = PROJECT_ROOT / "testes" / "data" / "extratos" / "extrato março 141air.csv"
    csv_text = csv_path.read_text(encoding="utf-8-sig")
    print(f"Ingesting {csv_path}")
    result = await ingest_extrato_from_csv(seller, csv_text, month)
    print("Result:")
    for k, v in result.items():
        if k in ("summary", "by_type"):
            continue
        print(f"  {k}: {v}")
    print(f"  by_type: {result.get('by_type')}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
