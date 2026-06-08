"""Fetch payments via ML API (read-only) p/ pares (slug, mes) faltantes.

Salva em testes/cache_{mon}2026/{slug}_payments.json (mesmo formato do rebuild_cache).
Usa range_fields date_approved + date_last_updated + money_release_date (cobre
liberações e refunds). Read-only: só search_payments.

Uso: python3 -m testes.harness.fetch_all
"""
import asyncio, json, os, sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from app.services import ml_api

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MONTH_RANGE = {
    "jan": ("2026-01-01", "2026-01-31"), "fev": ("2026-02-01", "2026-02-28"),
    "mar": ("2026-03-01", "2026-03-31"), "abr": ("2026-04-01", "2026-04-30"),
    "mai": ("2026-05-01", "2026-05-31"),
}
MONTH_DIR = {"jan": "cache_jan2026", "fev": "cache_fev2026", "mar": "cache_mar2026",
             "abr": "cache_abr2026", "mai": "cache_mai2026"}
RANGE_FIELDS = ["date_approved", "date_last_updated", "money_release_date"]

# pares faltantes
PAIRS = [
    ("141air", "mar"), ("141air", "abr"), ("141air", "mai"),
    ("net-air", "fev"), ("net-air", "mar"), ("net-air", "abr"), ("net-air", "mai"),
]


async def fetch_pages(slug, begin, end, rf):
    out, offset = [], 0
    while True:
        try:
            r = await ml_api.search_payments(slug, begin, end, offset=offset, limit=50, range_field=rf)
        except Exception as e:
            print(f"    erro offset={offset} {rf}: {e}", flush=True)
            break
        res = r.get("results", [])
        out.extend(res)
        total = r.get("paging", {}).get("total", 0)
        if not res or offset + len(res) >= total or offset >= 10000:
            break
        offset += 50
    return out


async def fetch_pair(slug, mes):
    begin = f"{MONTH_RANGE[mes][0]}T00:00:00.000-03:00"
    end = f"{MONTH_RANGE[mes][1]}T23:59:59.999-03:00"
    uniq = {}
    counts = {}
    for rf in RANGE_FIELDS:
        ps = await fetch_pages(slug, begin, end, rf)
        counts[rf] = len(ps)
        for p in ps:
            uniq[str(p["id"])] = p
        print(f"  {slug} {mes} {rf}: {len(ps)} (uniq total {len(uniq)})", flush=True)
    outdir = os.path.join(BASE, MONTH_DIR[mes])
    os.makedirs(outdir, exist_ok=True)
    path = os.path.join(outdir, f"{slug}_payments.json")
    with open(path, "w") as f:
        json.dump({"seller_slug": slug, "fetched_at": datetime.now(timezone.utc).isoformat(),
                   "counts": counts, "unique_total": len(uniq),
                   "payments": list(uniq.values())}, f, default=str)
    print(f"  SAVED {path}: {len(uniq)} payments", flush=True)


async def main():
    for slug, mes in PAIRS:
        path = os.path.join(BASE, MONTH_DIR[mes], f"{slug}_payments.json")
        if os.path.exists(path):
            print(f"skip {slug} {mes} (já existe)", flush=True)
            continue
        print(f"=== fetch {slug} {mes} ===", flush=True)
        await fetch_pair(slug, mes)


if __name__ == "__main__":
    asyncio.run(main())
