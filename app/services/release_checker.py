"""
Verifica money_release_status no ML antes de processar baixas.

Fluxo:
1. Parse descricoes das parcelas CA → extrai payment_ids e order_ids
2. Preload bulk do Supabase (raw_payment->money_release_status)
3. Re-check via ML API para payments com release_date passada mas status "pending"
4. Retorna mapa parcela_id → status para filtragem em baixas.py
"""
import logging
import re
from datetime import datetime

from app.db.supabase import get_db
from app.services import ml_api

logger = logging.getLogger(__name__)

# Regex patterns matching descriptions created by processor.py
PAYMENT_ID_RE = re.compile(r"Payment\s+(\d+)")
DEVOLUCAO_RE = re.compile(r"Devolu[çc][aã]o(?:\s+parcial)?\s+ML\s+#(\d+)")
ESTORNO_RE = re.compile(r"Estorno\s+taxa[s]?\s+ML\s+#(\d+)")
ORDER_ID_RE = re.compile(r"Venda\s+ML\s+#(\d+)")


def _parse_descricao(descricao: str) -> tuple[str, int | None]:
    """Extract identifier from parcela description.

    Returns (id_type, id_value) where id_type is "payment", "order", or "unknown".
    """
    if not descricao:
        return "unknown", None

    m = PAYMENT_ID_RE.search(descricao)
    if m:
        return "payment", int(m.group(1))

    m = DEVOLUCAO_RE.search(descricao)
    if m:
        return "payment", int(m.group(1))

    m = ESTORNO_RE.search(descricao)
    if m:
        return "payment", int(m.group(1))

    m = ORDER_ID_RE.search(descricao)
    if m:
        return "order", int(m.group(1))

    return "unknown", None


def _is_refund_or_estorno(descricao: str) -> bool:
    """Check if parcela is a refund/estorno (bypass release check)."""
    if not descricao:
        return False
    d = descricao.lower()
    return ("devolu" in d) or ("estorno" in d)


class ReleaseChecker:
    """Verifies money_release_status for parcelas before baixa."""

    def __init__(self, seller_slug: str):
        self.seller_slug = seller_slug
        self.db = get_db()
        # Cache: payment_id → {"status": str, "money_release_date": str|None}
        self._cache: dict[int, dict] = {}

    async def check_parcelas_batch(self, parcelas: list[dict]) -> dict[str, str]:
        """Check release status for a batch of parcelas.

        Returns {parcela_id: "released" | "pending" | "unknown" | "bypass"}.
        "bypass" means the parcela is a refund/estorno and should always be processed.
        """
        result: dict[str, str] = {}
        payment_ids: set[int] = set()
        order_ids: set[int] = set()
        # Map parcela_id → (id_type, id_value) for later lookup
        parcela_map: dict[str, tuple[str, int | None]] = {}

        # 1. Parse descriptions
        for p in parcelas:
            parcela_id = p.get("id", "")
            descricao = p.get("descricao", "")

            if _is_refund_or_estorno(descricao):
                result[parcela_id] = "bypass"
                continue

            id_type, id_value = _parse_descricao(descricao)
            parcela_map[parcela_id] = (id_type, id_value)

            if id_type == "payment" and id_value:
                payment_ids.add(id_value)
            elif id_type == "order" and id_value:
                order_ids.add(id_value)

        # 2. Preload from Supabase
        await self._preload(payment_ids, order_ids)

        # 3. Determine status for each parcela
        today = datetime.now().strftime("%Y-%m-%d")
        recheck_ids: set[int] = set()

        for parcela_id, (id_type, id_value) in parcela_map.items():
            if id_value is None:
                result[parcela_id] = "unknown"
                continue

            cached = self._cache.get(id_value)
            if not cached:
                result[parcela_id] = "unknown"
                continue

            status = cached["status"]
            mrd = cached.get("money_release_date")

            if status == "released":
                result[parcela_id] = "released"
            elif mrd and mrd <= today:
                # Release date passed but still pending → needs ML API re-check
                recheck_ids.add(id_value)
                result[parcela_id] = "__recheck__"
            else:
                result[parcela_id] = "pending"

        # 4. Re-check via ML API (deduplicated)
        if recheck_ids:
            refreshed = await self._recheck_ml_api(recheck_ids)
            for parcela_id, (id_type, id_value) in parcela_map.items():
                if result.get(parcela_id) == "__recheck__" and id_value:
                    result[parcela_id] = refreshed.get(id_value, "pending")

        return result

    async def _preload(self, payment_ids: set[int], order_ids: set[int]):
        """Bulk-load release status from Supabase payments table."""
        if payment_ids:
            try:
                rows = self.db.table("payments").select(
                    "ml_payment_id, raw_payment->money_release_status, money_release_date"
                ).eq("seller_slug", self.seller_slug).in_(
                    "ml_payment_id", list(payment_ids)
                ).execute()

                for r in rows.data or []:
                    pid = r["ml_payment_id"]
                    self._cache[pid] = {
                        "status": r.get("money_release_status") or "unknown",
                        "money_release_date": r.get("money_release_date"),
                    }
            except Exception as e:
                logger.warning(f"Preload by payment_id failed: {e}")

        if order_ids:
            try:
                rows = self.db.table("payments").select(
                    "ml_payment_id, ml_order_id, raw_payment->money_release_status, money_release_date"
                ).eq("seller_slug", self.seller_slug).in_(
                    "ml_order_id", list(order_ids)
                ).execute()

                for r in rows.data or []:
                    pid = r["ml_payment_id"]
                    oid = r.get("ml_order_id")
                    info = {
                        "status": r.get("money_release_status") or "unknown",
                        "money_release_date": r.get("money_release_date"),
                    }
                    self._cache[pid] = info
                    # Also index by order_id so parcelas with order ref can find it
                    if oid:
                        self._cache[oid] = info
            except Exception as e:
                logger.warning(f"Preload by order_id failed: {e}")

    async def _recheck_ml_api(self, payment_ids: set[int]) -> dict[int, str]:
        """Re-fetch payment from ML API and update Supabase if released."""
        results: dict[int, str] = {}

        for pid in payment_ids:
            try:
                payment = await ml_api.get_payment(self.seller_slug, pid)
                status = payment.get("money_release_status", "pending")
                results[pid] = status

                if status == "released":
                    # Update raw_payment in Supabase so next run uses cache
                    try:
                        self.db.table("payments").update({
                            "raw_payment": payment,
                            "updated_at": datetime.now().isoformat(),
                        }).eq("ml_payment_id", pid).eq(
                            "seller_slug", self.seller_slug
                        ).execute()
                        logger.info(f"Payment {pid} now released, updated Supabase")
                    except Exception as e:
                        logger.warning(f"Failed to update payment {pid} in Supabase: {e}")
                else:
                    logger.info(f"Payment {pid} still {status}, skipping baixa")

            except Exception as e:
                logger.warning(f"ML API re-check failed for payment {pid}: {e}")
                results[pid] = "pending"  # Conservative: skip on error

        return results
