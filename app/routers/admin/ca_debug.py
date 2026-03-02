"""
Conta Azul debug/resource endpoints: contas-financeiras, centros-custo, categories.
"""
import logging

from fastapi import APIRouter, Depends, HTTPException

from ._deps import require_admin

logger = logging.getLogger(__name__)
router = APIRouter()


# ── Conta Azul Resources ─────────────────────────────────────

@router.get("/ca/contas-financeiras", dependencies=[Depends(require_admin)])
async def list_ca_accounts():
    from app.services.ca_api import listar_contas_financeiras
    try:
        raw = await listar_contas_financeiras()
        logger.info(f"CA contas-financeiras: {len(raw)} items")
        return [{"id": acc["id"], "nome": acc.get("nome", ""), "tipo": acc.get("tipo", "")} for acc in raw]
    except Exception as e:
        logger.error(f"CA contas-financeiras error: {e}")
        raise HTTPException(status_code=502, detail=str(e))


@router.get("/ca/centros-custo", dependencies=[Depends(require_admin)])
async def list_ca_cost_centers():
    from app.services.ca_api import listar_centros_custo
    try:
        raw = await listar_centros_custo()
        logger.info(f"CA centros-custo: {len(raw)} items")
        return [{"id": cc["id"], "descricao": cc.get("nome", "")} for cc in raw]
    except Exception as e:
        logger.error(f"CA centros-custo error: {e}")
        raise HTTPException(status_code=502, detail=str(e))


@router.post("/ca/categories/sync", dependencies=[Depends(require_admin)])
async def trigger_ca_categories_sync():
    """Manually trigger a sync of CA income/expense categories to the local JSON file."""
    from app.services.ca_categories_sync import sync_ca_categories
    try:
        result = await sync_ca_categories()
        return result
    except Exception as e:
        logger.error("CA categories sync error: %s", e, exc_info=True)
        raise HTTPException(status_code=502, detail=f"CA categories sync failed: {e}")


@router.get("/ca/categories/status", dependencies=[Depends(require_admin)])
async def ca_categories_sync_status():
    """Return the status of the last CA categories sync."""
    from app.services.ca_categories_sync import get_last_sync_result
    return get_last_sync_result()


@router.get("/ca/categories", dependencies=[Depends(require_admin)])
async def list_ca_categories():
    """List all CA categories from the local file. Auto-fetches from CA API if file is missing."""
    from app.services.ca_categories_sync import load_categories, sync_ca_categories
    cats = load_categories()
    if not cats:
        await sync_ca_categories()
        cats = load_categories()
    return cats
