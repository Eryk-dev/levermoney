#!/usr/bin/env python3
"""
Rebuild cache de payments para simulacao DRE.
Busca por TODOS os criterios de data para garantir cobertura completa:
  - date_created (usado pelo ML Report CSV)
  - date_approved (usado pelo nosso DRE)
  - date_last_updated (pega refunds/chargebacks atualizados depois)
  - money_release_date (captura vendas aprovadas antes do periodo com release tardio)

Uso:
    cd "lever money claude v3"
    python3 testes/rebuild_cache.py --seller net-air
    python3 testes/rebuild_cache.py --seller net-air --begin 2026-01-01 --end 2026-01-31
    python3 testes/rebuild_cache.py --all
"""
import asyncio
import json
import sys
import argparse
import logging
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.services import ml_api

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("rebuild_cache")

CACHE_DIR = PROJECT_ROOT / "testes" / "cache_jan2026"

ALL_SELLERS = ["141air", "net-air", "netparts-sp", "easy-utilidades"]

# Periodos: busca dezembro (para cross-month) e janeiro
PERIODS = {
    "jan": ("2026-01-01T00:00:00.000-03:00", "2026-01-31T23:59:59.999-03:00"),
    "dec": ("2025-12-01T00:00:00.000-03:00", "2025-12-31T23:59:59.999-03:00"),
}

RANGE_FIELDS = ["date_created", "date_approved", "date_last_updated", "money_release_date"]


async def fetch_all_pages(seller_slug: str, begin: str, end: str, range_field: str) -> list[dict]:
    """Busca TODAS as paginas de um search_payments."""
    all_payments = []
    offset = 0
    limit = 50
    while True:
        try:
            result = await ml_api.search_payments(
                seller_slug, begin, end, offset=offset, limit=limit, range_field=range_field,
            )
        except Exception as exc:
            logger.error(f"  Erro em offset={offset}: {exc}")
            break

        results_list = result.get("results", [])
        paging = result.get("paging", {})
        total = paging.get("total", 0)

        all_payments.extend(results_list)

        fetched_so_far = offset + len(results_list)
        if len(results_list) == 0 or fetched_so_far >= total:
            break

        offset += limit

        # Safety: ML API caps at offset=1000 for some endpoints
        if offset >= 10000:
            logger.warning(f"  Hit offset limit 10000 for {range_field} {begin[:10]}")
            break

    return all_payments


async def rebuild_seller_cache(seller_slug: str, begin_override: str = None, end_override: str = None):
    """Rebuild cache para um seller."""
    logger.info(f"=== Rebuilding cache: {seller_slug} ===")

    all_payments = {}
    counts = {}

    for period_name, (default_begin, default_end) in PERIODS.items():
        begin = begin_override or default_begin
        end = end_override or default_end

        # Se override, so faz 1 periodo
        if begin_override and period_name != "jan":
            continue

        for range_field in RANGE_FIELDS:
            label = f"{range_field}_{period_name}"
            logger.info(f"  Fetching {label}: {begin[:10]} to {end[:10]}")

            payments = await fetch_all_pages(seller_slug, begin, end, range_field)
            new_count = 0
            for p in payments:
                pid = str(p["id"])
                if pid not in all_payments:
                    all_payments[pid] = p
                    new_count += 1

            counts[label] = len(payments)
            logger.info(f"    Got {len(payments)} payments, {new_count} new (total unique: {len(all_payments)})")

    # Save
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    output_path = CACHE_DIR / f"{seller_slug}_payments.json"

    cache_data = {
        "seller_slug": seller_slug,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "counts": counts,
        "unique_total": len(all_payments),
        "payments": list(all_payments.values()),
    }

    with open(output_path, "w") as f:
        json.dump(cache_data, f, default=str)

    size_mb = output_path.stat().st_size / 1024 / 1024
    logger.info(f"  Saved {output_path.name}: {len(all_payments)} unique payments ({size_mb:.1f} MB)")

    return len(all_payments)


async def main():
    parser = argparse.ArgumentParser(description="Rebuild payment cache")
    parser.add_argument("--seller", type=str, default=None)
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--begin", type=str, default=None, help="Override begin date (YYYY-MM-DD)")
    parser.add_argument("--end", type=str, default=None, help="Override end date (YYYY-MM-DD)")
    args = parser.parse_args()

    sellers = ALL_SELLERS if args.all else [args.seller or "net-air"]

    begin = f"{args.begin}T00:00:00.000-03:00" if args.begin else None
    end = f"{args.end}T23:59:59.999-03:00" if args.end else None

    for slug in sellers:
        total = await rebuild_seller_cache(slug, begin, end)
        logger.info(f"  {slug}: {total} unique payments cached\n")


if __name__ == "__main__":
    asyncio.run(main())
