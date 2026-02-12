"""
API Conciliador V2 - Sincronização ML/MP <-> Conta Azul
Unified platform: payment sync + faturamento sync + admin + dashboard API
"""
import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timedelta

import os
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.routers import health, webhooks, auth_ml, backfill, baixas, queue, admin, dashboard_api
from app.services.ca_queue import CaWorker
from app.services.faturamento_sync import FaturamentoSyncer
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


@asynccontextmanager
async def lifespan(app):
    await worker.start()
    await syncer.start()
    baixa_task = asyncio.create_task(_daily_baixa_scheduler())
    yield
    await worker.stop()
    await syncer.stop()
    baixa_task.cancel()


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
app.include_router(backfill.router)
app.include_router(baixas.router)
app.include_router(queue.router)

# New routers
app.include_router(admin.router)
app.include_router(dashboard_api.router)

# Serve dashboard static files (built React SPA)
DASHBOARD_DIR = Path(__file__).resolve().parent.parent / "dashboard-dist"

# API prefixes - these should NOT be handled by the dashboard catch-all
API_PREFIXES = (
    "admin", "dashboard", "auth", "health", "webhooks",
    "backfill", "baixas", "queue", "docs", "openapi.json", "redoc",
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
