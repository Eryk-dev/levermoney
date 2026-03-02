"""
Legacy export bridge endpoint (multipart file upload).
"""
import logging
from datetime import datetime

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse

from app.db.supabase import get_db
from app.models.sellers import get_seller_config
from app.routers.admin import require_admin
from app.services.legacy_bridge import build_legacy_expenses_zip, run_legacy_reconciliation
from ._deps import _default_legacy_centro_custo, _sanitize_path_component

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/{seller_slug}/legacy-export", dependencies=[Depends(require_admin)])
async def export_legacy_movements(
    seller_slug: str,
    extrato: UploadFile = File(..., description="Account statement (CSV or ZIP)"),
    dinheiro: UploadFile | None = File(None, description="Settlement report (CSV or ZIP)"),
    vendas: UploadFile | None = File(None, description="Collection report (CSV or ZIP)"),
    pos_venda: UploadFile | None = File(None, description="After-collection report (CSV or ZIP)"),
    liberacoes: UploadFile | None = File(None, description="Reserve-release report (CSV or ZIP)"),
    centro_custo: str | None = Form(None, description="Override for legacy center name in XLSX"),
):
    """
    Hybrid bridge: run legacy reconciliation and export only MP movement files.

    Output ZIP:
    - Conta Azul/PAGAMENTO_CONTAS.xlsx
    - Conta Azul/TRANSFERENCIAS.xlsx
    - Resumo/*_RESUMO.xlsx
    - Outros/*.csv
    """
    db = get_db()
    seller = get_seller_config(db, seller_slug)
    if not seller:
        raise HTTPException(status_code=404, detail=f"Seller {seller_slug} not found")

    centro = (centro_custo or "").strip() or _default_legacy_centro_custo(seller)

    try:
        resultado = await run_legacy_reconciliation(
            extrato=extrato,
            dinheiro=dinheiro,
            vendas=vendas,
            pos_venda=pos_venda,
            liberacoes=liberacoes,
            centro_custo=centro,
        )
        zip_buf, summary = build_legacy_expenses_zip(resultado)
    except FileNotFoundError as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
    except Exception as e:
        logger.exception("legacy-export failed for seller=%s", seller_slug)
        raise HTTPException(status_code=400, detail=f"Legacy export failed: {e}") from e

    company = _sanitize_path_component((seller.get("dashboard_empresa") or seller_slug).upper())
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"legacy_movimentos_{company}_{ts}.zip"

    return StreamingResponse(
        zip_buf,
        media_type="application/zip",
        headers={
            "Content-Disposition": f"attachment; filename={filename}",
            "X-Legacy-Centro-Custo": centro,
            "X-Legacy-Pagamentos-Rows": str(summary.get("pagamentos_rows", 0)),
            "X-Legacy-Transferencias-Rows": str(summary.get("transferencias_rows", 0)),
        },
    )
