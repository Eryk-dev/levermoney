import traceback
from fastapi import APIRouter

router = APIRouter()


@router.get("/health")
async def health():
    return {"status": "ok"}


@router.get("/debug/ca-token")
async def debug_ca_token():
    """Testa refresh do token CA e retorna status."""
    try:
        from app.services.ca_api import _get_ca_token
        token = await _get_ca_token()
        return {"status": "ok", "token_prefix": token[:20] + "...", "token_len": len(token)}
    except Exception as e:
        return {"status": "error", "error": str(e), "traceback": traceback.format_exc()}


@router.get("/debug/process-test")
async def debug_process_test():
    """Testa o processamento de 1 payment e retorna detalhes do erro se houver."""
    results = {}
    try:
        from app.db.supabase import get_db
        from app.models.sellers import get_seller_config
        db = get_db()
        results["db"] = "ok"

        seller = get_seller_config(db, "141air")
        results["seller"] = {"found": seller is not None, "has_contato": bool(seller.get("ca_contato_ml")) if seller else False}

        from app.services import ml_api
        payment = await ml_api.get_payment("141air", 144359445042)
        results["ml_payment"] = {"status": payment["status"], "amount": payment["transaction_amount"]}

        from app.services.ca_api import _get_ca_token
        token = await _get_ca_token()
        results["ca_token"] = f"{token[:20]}... (len={len(token)})"

        # Tenta criar receita de teste
        from app.services.processor import _build_parcela, _build_evento
        from app.models.sellers import CA_CATEGORIES

        parcela = _build_parcela("TEST", "2026-02-15", seller["ca_conta_mp_retido"], 0.01)
        payload = _build_evento(
            "2026-02-01", 0.01, "TEST - DELETAR", "teste debug",
            seller.get("ca_contato_ml"), seller["ca_conta_mp_retido"],
            CA_CATEGORIES["venda_ml"], seller.get("ca_centro_custo_variavel"), parcela,
        )
        results["payload"] = payload

        from app.services import ca_api
        ca_result = await ca_api.criar_conta_receber(payload)
        results["ca_create"] = {"status": "ok", "id": ca_result.get("id")}

    except Exception as e:
        results["error"] = str(e)
        results["traceback"] = traceback.format_exc()

    return results
