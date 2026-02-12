"""
Persistent CA job queue backed by Supabase.

Jobs are enqueued by processor.py and baixas.py, then executed
by CaWorker respecting the global rate limit.
"""
import asyncio
import logging
import httpx
from datetime import datetime, timezone

from app.db.supabase import get_db
from app.services.rate_limiter import rate_limiter
from app.services.ca_api import _headers, CA_API, _token_cache

logger = logging.getLogger(__name__)

# Retry backoff schedule in seconds
RETRY_BACKOFFS = [30, 120, 480]


# ---------------------------------------------------------------------------
# Enqueue
# ---------------------------------------------------------------------------

async def enqueue(
    seller_slug: str,
    job_type: str,
    ca_endpoint: str,
    ca_payload: dict,
    idempotency_key: str,
    group_id: str | None = None,
    priority: int = 20,
    ca_method: str = "POST",
    scheduled_for: str | None = None,
) -> dict:
    """Insert a job into ca_jobs. Returns existing row on idempotency conflict."""
    db = get_db()
    row = {
        "idempotency_key": idempotency_key,
        "seller_slug": seller_slug,
        "job_type": job_type,
        "ca_endpoint": ca_endpoint,
        "ca_method": ca_method,
        "ca_payload": ca_payload,
        "group_id": group_id,
        "priority": priority,
        "status": "pending",
        "scheduled_for": scheduled_for or datetime.now(timezone.utc).isoformat(),
    }

    try:
        result = db.table("ca_jobs").insert(row).execute()
        job = result.data[0] if result.data else row
        logger.info(f"Enqueued {job_type} for {seller_slug}: {idempotency_key}")
        return job
    except Exception as e:
        err_str = str(e)
        if "duplicate" in err_str.lower() or "unique" in err_str.lower() or "23505" in err_str:
            # Idempotency conflict — return existing
            existing = db.table("ca_jobs").select("*").eq(
                "idempotency_key", idempotency_key
            ).execute()
            if existing.data:
                logger.info(f"Job already exists: {idempotency_key} (status={existing.data[0]['status']})")
                return existing.data[0]
        raise


# ---------------------------------------------------------------------------
# Convenience wrappers (1:1 with processor call sites)
# ---------------------------------------------------------------------------

def _ep(path: str) -> str:
    return f"{CA_API}{path}"


async def enqueue_receita(seller_slug: str, payment_id: int, payload: dict) -> dict:
    return await enqueue(
        seller_slug=seller_slug,
        job_type="receita",
        ca_endpoint=_ep("/v1/financeiro/eventos-financeiros/contas-a-receber"),
        ca_payload=payload,
        idempotency_key=f"{seller_slug}:{payment_id}:receita",
        group_id=f"{seller_slug}:{payment_id}",
        priority=10,
    )


async def enqueue_comissao(seller_slug: str, payment_id: int, payload: dict) -> dict:
    return await enqueue(
        seller_slug=seller_slug,
        job_type="comissao",
        ca_endpoint=_ep("/v1/financeiro/eventos-financeiros/contas-a-pagar"),
        ca_payload=payload,
        idempotency_key=f"{seller_slug}:{payment_id}:comissao",
        group_id=f"{seller_slug}:{payment_id}",
        priority=20,
    )


async def enqueue_frete(seller_slug: str, payment_id: int, payload: dict) -> dict:
    return await enqueue(
        seller_slug=seller_slug,
        job_type="frete",
        ca_endpoint=_ep("/v1/financeiro/eventos-financeiros/contas-a-pagar"),
        ca_payload=payload,
        idempotency_key=f"{seller_slug}:{payment_id}:frete",
        group_id=f"{seller_slug}:{payment_id}",
        priority=20,
    )


async def enqueue_partial_refund(seller_slug: str, payment_id: int, index: int, payload: dict) -> dict:
    return await enqueue(
        seller_slug=seller_slug,
        job_type="partial_refund",
        ca_endpoint=_ep("/v1/financeiro/eventos-financeiros/contas-a-pagar"),
        ca_payload=payload,
        idempotency_key=f"{seller_slug}:{payment_id}:partial_refund:{index}",
        group_id=f"{seller_slug}:{payment_id}",
        priority=20,
    )


async def enqueue_estorno(seller_slug: str, payment_id: int, payload: dict) -> dict:
    return await enqueue(
        seller_slug=seller_slug,
        job_type="estorno",
        ca_endpoint=_ep("/v1/financeiro/eventos-financeiros/contas-a-pagar"),
        ca_payload=payload,
        idempotency_key=f"{seller_slug}:{payment_id}:estorno",
        group_id=f"{seller_slug}:{payment_id}",
        priority=20,
    )


async def enqueue_estorno_taxa(seller_slug: str, payment_id: int, payload: dict) -> dict:
    return await enqueue(
        seller_slug=seller_slug,
        job_type="estorno_taxa",
        ca_endpoint=_ep("/v1/financeiro/eventos-financeiros/contas-a-receber"),
        ca_payload=payload,
        idempotency_key=f"{seller_slug}:{payment_id}:estorno_taxa",
        group_id=f"{seller_slug}:{payment_id}",
        priority=20,
    )


async def enqueue_baixa(seller_slug: str, parcela_id: str, payload: dict,
                         scheduled_for: str | None = None) -> dict:
    return await enqueue(
        seller_slug=seller_slug,
        job_type="baixa",
        ca_endpoint=_ep(f"/v1/financeiro/eventos-financeiros/parcelas/{parcela_id}/baixa"),
        ca_payload=payload,
        idempotency_key=f"{seller_slug}:{parcela_id}:baixa",
        priority=30,
        scheduled_for=scheduled_for,
    )


# ---------------------------------------------------------------------------
# CaWorker — background task that processes the queue
# ---------------------------------------------------------------------------

class CaWorker:
    """Polls ca_jobs and executes them respecting the global rate limit."""

    def __init__(self, poll_interval: float = 1.0):
        self._poll_interval = poll_interval
        self._task: asyncio.Task | None = None
        self._running = False

    async def start(self):
        """Start the worker loop. Call during app lifespan startup."""
        self._running = True
        self._task = asyncio.create_task(self._loop())
        # Recover stuck jobs on startup
        await self._recover_stuck_jobs()
        logger.info("CaWorker started")

    async def stop(self):
        """Gracefully stop the worker."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("CaWorker stopped")

    async def _recover_stuck_jobs(self):
        """Reset jobs stuck in 'processing' for > 5 minutes back to 'failed'."""
        db = get_db()
        try:
            db.rpc("recover_stuck_ca_jobs", {}).execute()
        except Exception:
            # Fallback: manual update if RPC doesn't exist
            try:
                five_min_ago = datetime.now(timezone.utc).isoformat()
                db.table("ca_jobs").update({
                    "status": "failed",
                    "last_error": "Recovered: stuck in processing on startup",
                    "updated_at": five_min_ago,
                }).eq("status", "processing").lt(
                    "started_at", five_min_ago
                ).execute()
            except Exception as e:
                logger.warning(f"Could not recover stuck jobs: {e}")

    async def _loop(self):
        """Main worker loop."""
        while self._running:
            try:
                job = await self._poll_next_job()
                if not job:
                    await asyncio.sleep(self._poll_interval)
                    continue

                await rate_limiter.acquire()
                await self._execute_job(job)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"CaWorker loop error: {e}", exc_info=True)
                await asyncio.sleep(self._poll_interval)

    async def _poll_next_job(self) -> dict | None:
        """Fetch and atomically claim the next eligible job."""
        db = get_db()
        now = datetime.now(timezone.utc).isoformat()

        # Find next eligible job
        result = db.table("ca_jobs").select("*").in_(
            "status", ["pending", "failed"]
        ).lte("scheduled_for", now).or_(
            "next_retry_at.is.null,next_retry_at.lte." + now
        ).order("priority").order("created_at").limit(1).execute()

        if not result.data:
            return None

        job = result.data[0]

        # Atomic claim: only update if still in claimable state
        claim = db.table("ca_jobs").update({
            "status": "processing",
            "started_at": now,
            "attempts": job["attempts"] + 1,
            "updated_at": now,
        }).eq("id", job["id"]).in_(
            "status", ["pending", "failed"]
        ).execute()

        if not claim.data:
            return None  # Someone else claimed it

        return claim.data[0]

    async def _execute_job(self, job: dict):
        """Execute a single CA API job."""
        db = get_db()
        job_id = job["id"]
        now = datetime.now(timezone.utc).isoformat()

        try:
            headers = await _headers()
            method = job["ca_method"].lower()

            async with httpx.AsyncClient(timeout=30.0) as client:
                if method == "post":
                    resp = await client.post(job["ca_endpoint"], headers=headers, json=job["ca_payload"])
                else:
                    resp = await client.get(job["ca_endpoint"], headers=headers, params=job["ca_payload"])

            status_code = resp.status_code

            if 200 <= status_code < 300:
                # Success
                body = resp.json() if resp.content else {}
                db.table("ca_jobs").update({
                    "status": "completed",
                    "ca_response_status": status_code,
                    "ca_response_body": body,
                    "ca_protocolo": body.get("protocolo"),
                    "completed_at": now,
                    "updated_at": now,
                }).eq("id", job_id).execute()

                logger.info(f"Job {job_id} completed: {job['job_type']} protocolo={body.get('protocolo')}")

                # Check if all jobs in group are completed
                if job.get("group_id"):
                    await self._check_group_completion(job["group_id"])

            elif status_code == 401:
                # Token expired — invalidate and retry
                _token_cache["access_token"] = None
                _token_cache["expires_at"] = 0
                self._mark_retryable(db, job, f"401 Unauthorized", now)

            elif status_code == 429 or status_code >= 500:
                # Retryable server error
                body_text = resp.text[:500]
                self._mark_retryable(db, job, f"{status_code}: {body_text}", now)

            else:
                # Permanent client error (4xx) → dead letter
                body_text = resp.text[:1000]
                db.table("ca_jobs").update({
                    "status": "dead",
                    "ca_response_status": status_code,
                    "ca_response_body": {"error": body_text},
                    "last_error": f"{status_code}: {body_text}",
                    "completed_at": now,
                    "updated_at": now,
                }).eq("id", job_id).execute()
                logger.error(f"Job {job_id} dead: {status_code} {body_text[:200]}")

        except Exception as e:
            self._mark_retryable(db, job, str(e)[:500], now)

    def _mark_retryable(self, db, job: dict, error: str, now: str):
        """Mark job as failed with exponential backoff, or dead if max attempts reached."""
        attempts = job["attempts"]  # Already incremented in claim
        max_attempts = job.get("max_attempts", 3)

        if attempts >= max_attempts:
            db.table("ca_jobs").update({
                "status": "dead",
                "last_error": error,
                "updated_at": now,
            }).eq("id", job["id"]).execute()
            logger.error(f"Job {job['id']} dead after {attempts} attempts: {error}")
            return

        backoff_idx = min(attempts - 1, len(RETRY_BACKOFFS) - 1)
        backoff_secs = RETRY_BACKOFFS[backoff_idx]
        retry_at = datetime.now(timezone.utc).timestamp() + backoff_secs
        retry_iso = datetime.fromtimestamp(retry_at, tz=timezone.utc).isoformat()

        db.table("ca_jobs").update({
            "status": "failed",
            "last_error": error,
            "next_retry_at": retry_iso,
            "updated_at": now,
        }).eq("id", job["id"]).execute()
        logger.warning(f"Job {job['id']} failed (attempt {attempts}), retry at +{backoff_secs}s: {error}")

    async def _check_group_completion(self, group_id: str):
        """When all jobs in a group are completed, mark the payment as synced."""
        db = get_db()
        # Count non-completed jobs in this group
        pending = db.table("ca_jobs").select("id", count="exact").eq(
            "group_id", group_id
        ).not_.in_("status", ["completed", "dead"]).execute()

        if pending.count and pending.count > 0:
            return

        # All done — extract seller_slug and payment_id from group_id
        parts = group_id.split(":")
        if len(parts) >= 2:
            seller_slug, payment_id = parts[0], parts[1]
            try:
                db.table("payments").update({
                    "status": "synced",
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }).eq("ml_payment_id", int(payment_id)).eq(
                    "seller_slug", seller_slug
                ).eq("status", "queued").execute()
                logger.info(f"Group {group_id} completed — payment marked synced")
            except Exception as e:
                logger.warning(f"Could not update payment for group {group_id}: {e}")
