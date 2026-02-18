"""
Faturamento Sync Service - ported from dashatt/main.py.
Polls ML paid orders and upserts daily totals to faturamento table.
Uses unified sellers table instead of env vars.
"""
import asyncio
import logging
from datetime import datetime, timedelta, timezone

from app.db.supabase import get_db
from app.services.ml_api import fetch_paid_orders

logger = logging.getLogger(__name__)

BRT = timezone(timedelta(hours=-3))


class FaturamentoSyncer:
    def __init__(self, interval_minutes: int = 5):
        self.interval = interval_minutes
        self._task: asyncio.Task | None = None
        self._last_sync: str | None = None
        self._last_results: list[dict] = []

    async def start(self):
        self._task = asyncio.create_task(self._scheduler())
        logger.info("FaturamentoSyncer started (interval=%dm)", self.interval)

    async def stop(self):
        if self._task:
            self._task.cancel()
            logger.info("FaturamentoSyncer stopped")

    @property
    def last_sync(self) -> str | None:
        return self._last_sync

    @property
    def last_results(self) -> list[dict]:
        return self._last_results

    async def _scheduler(self):
        await self.sync_all()
        while True:
            await asyncio.sleep(self.interval * 60)
            try:
                await self.sync_all()
            except Exception:
                logger.exception("FaturamentoSyncer scheduler error")

    def _get_syncable_sellers(self) -> list[dict]:
        """Get all active sellers that have dashboard_empresa set and ML tokens."""
        db = get_db()
        result = db.table("sellers").select("*").eq("active", True).not_.is_("dashboard_empresa", "null").not_.is_("ml_user_id", "null").execute()
        return result.data or []

    async def sync_all(self) -> list[dict]:
        now_brt = datetime.now(BRT)
        date_str = now_brt.strftime("%Y-%m-%d")
        sellers = self._get_syncable_sellers()
        results: list[dict] = []

        logger.info("Faturamento sync starting for %s (%d sellers)", date_str, len(sellers))

        for seller in sellers:
            empresa = seller["dashboard_empresa"]
            slug = seller["slug"]
            try:
                data = await fetch_paid_orders(slug, date_str)

                if data["valor"] > 0:
                    ok = self._upsert_faturamento(empresa, date_str, data["valor"])
                    status = "synced" if ok else "upsert_error"
                else:
                    status = "no_sales"

                result = {
                    "empresa": empresa,
                    "date": date_str,
                    "valor": data["valor"],
                    "orders": data["order_count"],
                    "fraud_skipped": data["fraud_skipped"],
                    "status": status,
                }
                results.append(result)
                logger.info("[%s] %s: R$ %.2f (%d orders)", slug, status, data["valor"], data["order_count"])

            except Exception as e:
                logger.exception("[%s] Faturamento sync failed", slug)
                results.append({"empresa": empresa, "date": date_str, "status": "error", "error": str(e)})

        self._last_sync = now_brt.isoformat()
        self._last_results = results
        logger.info("Faturamento sync complete: %d sellers", len(results))
        return results

    def _upsert_faturamento(self, empresa: str, date_str: str, valor: float) -> bool:
        """Upsert to faturamento table using Supabase SDK."""
        try:
            db = get_db()
            db.table("faturamento").upsert(
                {"empresa": empresa, "data": date_str, "valor": valor, "source": "sync", "updated_at": datetime.now(BRT).isoformat()},
                on_conflict="empresa,data",
            ).execute()
            return True
        except Exception as e:
            logger.error("Supabase upsert failed %s/%s: %s", empresa, date_str, e)
            return False
