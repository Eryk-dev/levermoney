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

        import httpx as _httpx
        from app.services.ca_api import _get_ca_token as _gct
        _tk = await _gct()
        async with _httpx.AsyncClient(timeout=30.0) as _c:
            _r = await _c.post(
                "https://api-v2.contaazul.com/v1/financeiro/eventos-financeiros/contas-a-receber",
                headers={"Authorization": f"Bearer {_tk}", "Content-Type": "application/json"},
                json=payload,
            )
            results["ca_status"] = _r.status_code
            try:
                results["ca_response"] = _r.json()
            except Exception:
                results["ca_response"] = _r.text

    except Exception as e:
        results["error"] = str(e)
        results["traceback"] = traceback.format_exc()

    return results


@router.get("/debug/busca-parcela")
async def debug_busca_parcela():
    """Testa a busca de parcelas no CA para debug da baixa."""
    results = {}
    try:
        import httpx as _httpx
        from app.services.ca_api import _get_ca_token
        token = await _get_ca_token()

        # Testar endpoint de busca de parcelas
        async with _httpx.AsyncClient(timeout=30.0) as _c:
            _r = await _c.get(
                "https://api-v2.contaazul.com/v1/financeiro/parcelas/contas-a-pagar",
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                params={
                    "descricao": "Comissão ML - Payment 144359445042",
                    "data_vencimento_de": "2026-02-01",
                    "data_vencimento_ate": "2026-02-01",
                    "tamanho_pagina": 5,
                },
            )
            results["status_code"] = _r.status_code
            try:
                results["response"] = _r.json()
            except Exception:
                results["response_text"] = _r.text

    except Exception as e:
        results["error"] = str(e)
        results["traceback"] = traceback.format_exc()

    return results
