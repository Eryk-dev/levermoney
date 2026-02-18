"""
API Conciliador V2 - Sincronização ML/MP <-> Conta Azul
Unified platform: payment sync + faturamento sync + admin + dashboard API
"""
import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timedelta

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.routers import health, webhooks, auth_ml, auth_ca, backfill, baixas, queue, admin, dashboard_api, expenses
from app.services.ca_queue import CaWorker
from app.services.faturamento_sync import FaturamentoSyncer
from app.services.daily_sync import _daily_sync_scheduler, sync_all_sellers
from app.services.financial_closing import run_financial_closing_for_all
from app.services.legacy_daily_export import _legacy_daily_scheduler, run_legacy_daily_for_all
from app.db.supabase import get_db
from app.models.sellers import get_all_active_sellers
from app.routers.baixas import processar_baixas_auto

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
# Silence httpx per-request logs (floods terminal with ca_jobs polling)
logging.getLogger("httpx").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)

worker = CaWorker()
syncer = FaturamentoSyncer(interval_minutes=settings.sync_interval_minutes)

# Wire syncer reference into admin router for trigger/status endpoints
admin.set_syncer(syncer)


def _parse_weekdays(raw: str) -> set[int]:
    weekdays: set[int] = set()
    for part in (raw or "").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            value = int(part)
        except ValueError:
            continue
        if 0 <= value <= 6:
            weekdays.add(value)
    return weekdays


async def _daily_baixa_scheduler():
    """Run baixas daily at 10:00 BRT (13:00 UTC).
    On startup, runs immediately if current time is past 10:00 BRT."""
    try:
        from zoneinfo import ZoneInfo
    except ImportError:
        from backports.zoneinfo import ZoneInfo

    brt = ZoneInfo("America/Sao_Paulo")
    target_hour = 10

    # On startup: if already past 10:00 BRT today, run immediately
    now_brt = datetime.now(brt)
    if now_brt.hour >= target_hour:
        logger.info("Scheduler: past 10:00 BRT, running baixas now")
        await _run_baixas_all_sellers()

    while True:
        now_brt = datetime.now(brt)
        # Calculate seconds until next 10:00 BRT
        if now_brt.hour < target_hour:
            target = now_brt.replace(hour=target_hour, minute=0, second=0, microsecond=0)
        else:
            # Already past today, wait for tomorrow
            target = (now_brt + timedelta(days=1)).replace(
                hour=target_hour, minute=0, second=0, microsecond=0
            )

        wait_seconds = (target - now_brt).total_seconds()
        logger.info(f"Scheduler: next baixas run in {wait_seconds:.0f}s ({target.isoformat()})")

        await asyncio.sleep(wait_seconds)
        await _run_baixas_all_sellers()


async def _run_baixas_all_sellers():
    """Run processar_baixas_auto for each active seller."""
    try:
        db = get_db()
        sellers = get_all_active_sellers(db)
        for seller in sellers:
            slug = seller["slug"]
            try:
                result = await processar_baixas_auto(slug)
                logger.info(f"Scheduler baixas for {slug}: {result}")
            except Exception as e:
                logger.error(f"Scheduler baixas error for {slug}: {e}")
    except Exception as e:
        logger.error(f"Scheduler _run_baixas_all_sellers error: {e}")


async def _ca_token_refresh_loop():
    """Proactively refresh CA token every 30 min to keep rotation alive."""
    from app.services.ca_api import _get_ca_token
    while True:
        await asyncio.sleep(30 * 60)  # 30 minutes
        try:
            await _get_ca_token()
            logger.info("CA proactive token refresh OK")
        except Exception as e:
            logger.error(f"CA proactive token refresh failed: {e}")


async def _run_financial_closing():
    """Run financial closing for all active sellers using default D-1 window."""
    try:
        summary = await run_financial_closing_for_all()
        logger.info(
            "FinancialClosing: sellers=%s closed=%s open=%s window=%s..%s",
            summary.get("sellers_total", 0),
            summary.get("sellers_closed", 0),
            summary.get("sellers_open", 0),
            summary.get("date_from"),
            summary.get("date_to"),
        )
    except Exception as e:
        logger.error("FinancialClosing run error: %s", e, exc_info=True)


async def _financial_closing_scheduler():
    """Run financial closing daily at 11:30 BRT."""
    try:
        from zoneinfo import ZoneInfo
    except ImportError:
        from backports.zoneinfo import ZoneInfo

    brt = ZoneInfo("America/Sao_Paulo")
    target_hour = 11
    target_minute = 30

    # On startup: if already past today's run time, run immediately.
    now_brt = datetime.now(brt)
    if (now_brt.hour, now_brt.minute) >= (target_hour, target_minute):
        logger.info("FinancialClosing scheduler: past 11:30 BRT, running now")
        await _run_financial_closing()

    while True:
        now_brt = datetime.now(brt)
        if (now_brt.hour, now_brt.minute) < (target_hour, target_minute):
            target = now_brt.replace(
                hour=target_hour, minute=target_minute, second=0, microsecond=0
            )
        else:
            target = (now_brt + timedelta(days=1)).replace(
                hour=target_hour, minute=target_minute, second=0, microsecond=0
            )

        wait_seconds = (target - now_brt).total_seconds()
        logger.info(
            "FinancialClosing scheduler: next run in %.0fs (%s)",
            wait_seconds,
            target.isoformat(),
        )
        await asyncio.sleep(wait_seconds)
        await _run_financial_closing()


async def _run_nightly_pipeline():
    """Sequential nightly pipeline focused on daily close accuracy."""
    try:
        from zoneinfo import ZoneInfo
    except ImportError:
        from backports.zoneinfo import ZoneInfo

    now_brt = datetime.now(ZoneInfo("America/Sao_Paulo"))
    target_day = (now_brt - timedelta(days=1)).strftime("%Y-%m-%d")
    legacy_weekdays = _parse_weekdays(settings.nightly_pipeline_legacy_weekdays)
    if not legacy_weekdays:
        legacy_weekdays = {0, 3}

    logger.info("NightlyPipeline: start target_day=%s", target_day)

    try:
        sync_results = await sync_all_sellers()
        logger.info("NightlyPipeline: sync_all_sellers done (sellers=%s)", len(sync_results))
    except Exception as e:
        logger.error("NightlyPipeline: sync_all_sellers failed: %s", e, exc_info=True)

    try:
        await _run_baixas_all_sellers()
        logger.info("NightlyPipeline: baixas done")
    except Exception as e:
        logger.error("NightlyPipeline: baixas failed: %s", e, exc_info=True)

    if now_brt.weekday() in legacy_weekdays:
        try:
            legacy_results = await run_legacy_daily_for_all(target_day=target_day, upload=True)
            logger.info("NightlyPipeline: legacy daily done (sellers=%s)", len(legacy_results))
        except Exception as e:
            logger.error("NightlyPipeline: legacy daily failed: %s", e, exc_info=True)
    else:
        logger.info(
            "NightlyPipeline: skipping legacy daily today (weekday=%s, allowed=%s)",
            now_brt.weekday(),
            sorted(legacy_weekdays),
        )

    await _run_financial_closing()
    logger.info("NightlyPipeline: finished target_day=%s", target_day)


async def _nightly_pipeline_scheduler():
    """Run nightly pipeline once per day at configured BRT time."""
    try:
        from zoneinfo import ZoneInfo
    except ImportError:
        from backports.zoneinfo import ZoneInfo

    brt = ZoneInfo("America/Sao_Paulo")
    target_hour = max(0, min(23, int(settings.nightly_pipeline_hour_brt)))
    target_minute = max(0, min(59, int(settings.nightly_pipeline_minute_brt)))

    while True:
        now_brt = datetime.now(brt)
        if (now_brt.hour, now_brt.minute) < (target_hour, target_minute):
            target = now_brt.replace(hour=target_hour, minute=target_minute, second=0, microsecond=0)
        else:
            target = (now_brt + timedelta(days=1)).replace(
                hour=target_hour, minute=target_minute, second=0, microsecond=0
            )

        wait_seconds = (target - now_brt).total_seconds()
        logger.info("NightlyPipeline scheduler: next run in %.0fs (%s)", wait_seconds, target.isoformat())
        await asyncio.sleep(wait_seconds)
        await _run_nightly_pipeline()


@asynccontextmanager
async def lifespan(app):
    await worker.start()
    await syncer.start()
    ca_refresh_task = asyncio.create_task(_ca_token_refresh_loop())
    baixa_task = None
    daily_sync_task = None
    closing_task = None
    legacy_daily_task = None
    nightly_pipeline_task = None

    if settings.nightly_pipeline_enabled:
        nightly_pipeline_task = asyncio.create_task(_nightly_pipeline_scheduler())
    else:
        baixa_task = asyncio.create_task(_daily_baixa_scheduler())
        daily_sync_task = asyncio.create_task(_daily_sync_scheduler())
        closing_task = asyncio.create_task(_financial_closing_scheduler())
        if settings.legacy_daily_enabled:
            legacy_daily_task = asyncio.create_task(_legacy_daily_scheduler())
    yield
    await worker.stop()
    await syncer.stop()
    ca_refresh_task.cancel()
    if baixa_task:
        baixa_task.cancel()
    if daily_sync_task:
        daily_sync_task.cancel()
    if closing_task:
        closing_task.cancel()
    if legacy_daily_task:
        legacy_daily_task.cancel()
    if nightly_pipeline_task:
        nightly_pipeline_task.cancel()


app = FastAPI(
    title="API Conciliador V2",
    description="Sincronização automática ML/MP → Conta Azul + Dashboard Faturamento",
    version="2.1.0",
    lifespan=lifespan,
)

# CORS for dashboard
origins = [o.strip() for o in settings.cors_origins.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Existing routers
app.include_router(health.router)
app.include_router(webhooks.router)
app.include_router(auth_ml.router)
app.include_router(auth_ca.router)
app.include_router(backfill.router)
app.include_router(baixas.router)
app.include_router(queue.router)

# New routers
app.include_router(admin.router)
app.include_router(dashboard_api.router)
if settings.expenses_api_enabled:
    app.include_router(expenses.router)

# Serve dashboard static files (built React SPA)
DASHBOARD_DIR = Path(__file__).resolve().parent.parent / "dashboard-dist"

# API prefixes - these should NOT be handled by the dashboard catch-all
API_PREFIXES = (
    "admin", "dashboard", "auth", "health", "webhooks",
    "backfill", "baixas", "queue", "expenses", "docs", "openapi.json", "redoc",
    "install",
)

STATIC_DIR = Path(__file__).resolve().parent / "static"


@app.get("/install")
async def serve_install():
    """Serve the self-service install landing page."""
    return FileResponse(STATIC_DIR / "install.html")


if DASHBOARD_DIR.is_dir():
    app.mount("/assets", StaticFiles(directory=DASHBOARD_DIR / "assets"), name="dashboard-assets")

    @app.get("/{path:path}")
    async def serve_dashboard(request: Request, path: str):
        """Serve dashboard SPA - skip API routes, fallback to index.html."""
        # Don't intercept API routes
        first_segment = path.split("/")[0] if path else ""
        if first_segment in API_PREFIXES:
            from fastapi.responses import JSONResponse
            return JSONResponse({"detail": "Not Found"}, status_code=404)

        file = DASHBOARD_DIR / path
        if file.is_file():
            return FileResponse(file)
        return FileResponse(DASHBOARD_DIR / "index.html")
