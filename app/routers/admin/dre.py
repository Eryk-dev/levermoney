"""DRE por competência + pontes de reconciliação (event ledger)."""
from fastapi import APIRouter, Depends, Query

from app.services.dre_report import build_dre_monthly
from app.services.pontes import build_pontes
from ._deps import require_admin

router = APIRouter()


@router.get("/dre/{seller_slug}", dependencies=[Depends(require_admin)])
async def dre_seller(
    seller_slug: str,
    data_de: str = Query(..., description="YYYY-MM-DD (use folga p/ trás: refunds de vendas antigas)"),
    data_ate: str = Query(..., description="YYYY-MM-DD"),
):
    """DRE mensal por competência. Devoluções no mês do ESTORNO (event_date);
    receita/comissão/frete no mês da VENDA (competencia_date)."""
    return {
        "seller": seller_slug, "de": data_de, "ate": data_ate,
        "dre": await build_dre_monthly(seller_slug, data_de, data_ate),
    }


@router.get("/pontes/{seller_slug}", dependencies=[Depends(require_admin)])
async def pontes_seller(
    seller_slug: str,
    data_de: str = Query(..., description="YYYY-MM-DD"),
    data_ate: str = Query(..., description="YYYY-MM-DD"),
):
    """Pontes caixa↔DRE (Δ recebíveis) e DRE↔painel ML (devolução diferida)."""
    return await build_pontes(seller_slug, data_de, data_ate)
