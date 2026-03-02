"""
Expenses API package — list, export XLSX, stats, closing, and legacy bridge
for non-order MP payments. Protected by admin token.

Re-exports `router` so that `from app.routers.expenses import router` keeps working.
"""
from fastapi import APIRouter

from .crud import router as crud_router
from .export import router as export_router
from .closing import router as closing_router
from .legacy import router as legacy_router

router = APIRouter(prefix="/expenses", tags=["expenses"])
router.include_router(crud_router)
router.include_router(export_router)
router.include_router(closing_router)
router.include_router(legacy_router)
