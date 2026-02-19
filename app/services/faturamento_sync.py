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
                    ok, upsert_error = await self._upsert_faturamento(empresa, date_str, data["valor"])
                    status = "synced" if ok else "upsert_error"
                else:
                    upsert_error = None
                    status = "no_sales"

                result = {
                    "empresa": empresa,
                    "date": date_str,
                    "valor": data["valor"],
                    "orders": data["order_count"],
                    "fraud_skipped": data["fraud_skipped"],
                    "status": status,
                }
                if upsert_error:
                    # Keep payload small for admin API response.
                    result["error"] = str(upsert_error)[:400]
                results.append(result)
                logger.info("[%s] %s: R$ %.2f (%d orders)", slug, status, data["valor"], data["order_count"])

            except Exception as e:
                logger.exception("[%s] Faturamento sync failed", slug)
                results.append({"empresa": empresa, "date": date_str, "status": "error", "error": str(e)})

        self._last_sync = now_brt.isoformat()
        self._last_results = results
        logger.info("Faturamento sync complete: %d sellers", len(results))
        return results

    async def _upsert_faturamento(self, empresa: str, date_str: str, valor: float) -> tuple[bool, str | None]:
        """Upsert to faturamento with retries and fallback update/insert.

        This avoids hard-failing the whole sync on transient PostgREST/network
        issues and handles environments where on_conflict may intermittently fail.
        """
        db = get_db()
        payload = {
            "empresa": empresa,
            "data": date_str,
            "valor": valor,
            "source": "sync",
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        last_error: str | None = None

        for attempt in range(1, 4):
            try:
                db.table("faturamento").upsert(
                    payload,
                    on_conflict="empresa,data",
                ).execute()
                return True, None
            except Exception as e:
                last_error = str(e)
                logger.warning(
                    "Faturamento upsert attempt %d failed for %s/%s: %s",
                    attempt,
                    empresa,
                    date_str,
                    e,
                )

                # Fallback path: explicit update-or-insert without ON CONFLICT.
                try:
                    existing = (
                        db.table("faturamento")
                        .select("id")
                        .eq("empresa", empresa)
                        .eq("data", date_str)
                        .limit(1)
                        .execute()
                    )
                    if existing.data:
                        row_id = existing.data[0]["id"]
                        db.table("faturamento").update(payload).eq("id", row_id).execute()
                    else:
                        db.table("faturamento").insert(payload).execute()
                    return True, None
                except Exception as fallback_exc:
                    last_error = f"{e} | fallback={fallback_exc}"
                    logger.warning(
                        "Faturamento fallback attempt %d failed for %s/%s: %s",
                        attempt,
                        empresa,
                        date_str,
                        fallback_exc,
                    )

                if attempt < 3:
                    await asyncio.sleep(0.7 * attempt)

        logger.error("Supabase upsert failed %s/%s after retries: %s", empresa, date_str, last_error)
        return False, last_error
