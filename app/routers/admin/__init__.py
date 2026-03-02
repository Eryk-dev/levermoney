"""
Admin API - password-protected endpoints for seller management, goals, sync.
Authentication via X-Admin-Token header verified against bcrypt hash in admin_config table.

This package splits the admin router into sub-modules for maintainability.
The main `router` object is assembled here and re-exported so that existing
imports (e.g. `from app.routers.admin import router`) continue to work.
"""
from fastapi import APIRouter

from .auth import router as auth_router
from .sellers import router as sellers_router
from .closing import router as closing_router
from .legacy import router as legacy_router
from .extrato import router as extrato_router
from .release_report import router as release_report_router
from .revenue import router as revenue_router
from .ca_debug import router as ca_debug_router

# Re-export set_syncer so main.py can call admin.set_syncer(syncer)
# Re-export require_admin so expenses.py can do `from app.routers.admin import require_admin`
from ._deps import set_syncer, require_admin  # noqa: F401

router = APIRouter(prefix="/admin", tags=["admin"])

router.include_router(auth_router)
router.include_router(sellers_router)
router.include_router(closing_router)
router.include_router(legacy_router)
router.include_router(extrato_router)
router.include_router(release_report_router)
router.include_router(revenue_router)
router.include_router(ca_debug_router)
