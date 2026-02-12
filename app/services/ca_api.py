"""
Cliente para API do Conta Azul v2.
Base: https://api-v2.contaazul.com
Rate limit: 600 req/min, 10 req/seg

Tokens armazenados no Supabase (tabela ca_tokens).
Auto-refresh via AWS Cognito quando expirado.
"""
import asyncio
import logging
import time
import httpx
from app.db.supabase import get_db
from app.services.rate_limiter import rate_limiter

logger = logging.getLogger(__name__)

CA_API = "https://api-v2.contaazul.com"
COGNITO_URL = "https://cognito-idp.sa-east-1.amazonaws.com/"
COGNITO_CLIENT_ID = "6ri07ptg5k2u7dubdlttg3a7t8"

# Cache em memória para evitar query a cada request
_token_cache = {
    "access_token": None,
    "expires_at": 0,
}
# Lock to prevent concurrent Cognito refresh (race condition → 400 errors)
_refresh_lock = asyncio.Lock()


async def _get_ca_token() -> str:
    """Pega access_token do CA. Se expirado, faz refresh via Cognito.
    Uses asyncio.Lock to prevent concurrent refresh attempts."""
    now_ms = int(time.time() * 1000)

    # Fast path: cache still valid (60s margin)
    if _token_cache["access_token"] and _token_cache["expires_at"] > now_ms + 60000:
        return _token_cache["access_token"]

    async with _refresh_lock:
        # Re-check after acquiring lock (another coroutine may have refreshed)
        now_ms = int(time.time() * 1000)
        if _token_cache["access_token"] and _token_cache["expires_at"] > now_ms + 60000:
            return _token_cache["access_token"]

        db = get_db()
        result = db.table("ca_tokens").select("*").eq("id", 1).single().execute()
        tokens = result.data

        if not tokens:
            raise RuntimeError("CA tokens not found in Supabase ca_tokens table")

        # If Supabase token still valid (another process may have refreshed)
        if tokens["expires_at"] > now_ms + 60000:
            _token_cache["access_token"] = tokens["access_token"]
            _token_cache["expires_at"] = tokens["expires_at"]
            return tokens["access_token"]

        # Refresh via Cognito
        logger.info("CA token expired, refreshing via Cognito...")
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                COGNITO_URL,
                headers={
                    "Content-Type": "application/x-amz-json-1.1",
                    "X-Amz-Target": "AWSCognitoIdentityProviderService.InitiateAuth",
                },
                json={
                    "AuthFlow": "REFRESH_TOKEN_AUTH",
                    "ClientId": COGNITO_CLIENT_ID,
                    "AuthParameters": {
                        "REFRESH_TOKEN": tokens["refresh_token"],
                    },
                },
            )
            resp.raise_for_status()
            data = resp.json()

        auth_result = data.get("AuthenticationResult", {})
        new_access_token = auth_result.get("AccessToken")
        expires_in = auth_result.get("ExpiresIn", 3600)

        if not new_access_token:
            raise RuntimeError(f"Cognito refresh failed: {data}")

        new_expires_at = int(time.time() * 1000) + (expires_in * 1000)

        # Update Supabase
        db.table("ca_tokens").update({
            "access_token": new_access_token,
            "expires_at": new_expires_at,
        }).eq("id", 1).execute()

        # Update cache
        _token_cache["access_token"] = new_access_token
        _token_cache["expires_at"] = new_expires_at

        logger.info(f"CA token refreshed, expires in {expires_in}s")
        return new_access_token


async def _headers() -> dict:
    token = await _get_ca_token()
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }


async def _request_with_retry(method: str, url: str, max_retries: int = 3, **kwargs) -> httpx.Response:
    """HTTP request with automatic retry on 401 (re-auth), 429, 5xx.
    Respects global rate limit shared with CaWorker."""
    await rate_limiter.acquire()
    for attempt in range(max_retries + 1):
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await getattr(client, method)(url, **kwargs)

            if resp.status_code == 401 and attempt < max_retries:
                # Token expired mid-flight → invalidate cache, get fresh token, retry
                logger.warning(f"CA 401 on {method.upper()} {url}, refreshing token...")
                _token_cache["access_token"] = None
                _token_cache["expires_at"] = 0
                kwargs["headers"] = await _headers()
                await asyncio.sleep(0.5)
                continue

            if resp.status_code == 429 or resp.status_code >= 500:
                if attempt < max_retries:
                    wait = (attempt + 1) * 1.0
                    logger.warning(f"CA {resp.status_code} on {method.upper()} {url}, retry {attempt+1} in {wait}s")
                    await asyncio.sleep(wait)
                    continue
            resp.raise_for_status()
            return resp
    raise RuntimeError(f"Max retries exceeded for {url}")


async def criar_conta_receber(payload: dict) -> dict:
    """POST /v1/financeiro/eventos-financeiros/contas-a-receber"""
    resp = await _request_with_retry(
        "post", f"{CA_API}/v1/financeiro/eventos-financeiros/contas-a-receber",
        headers=await _headers(), json=payload,
    )
    return resp.json()


async def criar_conta_pagar(payload: dict) -> dict:
    """POST /v1/financeiro/eventos-financeiros/contas-a-pagar"""
    resp = await _request_with_retry(
        "post", f"{CA_API}/v1/financeiro/eventos-financeiros/contas-a-pagar",
        headers=await _headers(), json=payload,
    )
    return resp.json()


async def listar_parcelas_evento(evento_id: str) -> list:
    """GET /v1/financeiro/eventos-financeiros/{id}/parcelas"""
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(
            f"{CA_API}/v1/financeiro/eventos-financeiros/{evento_id}/parcelas",
            headers=await _headers(),
        )
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, list):
            return data
        if isinstance(data, dict) and "items" in data:
            return data["items"]
        return [data] if data else []


async def buscar_parcelas_pagar(descricao: str, data_venc_de: str, data_venc_ate: str) -> list:
    """GET /v1/financeiro/eventos-financeiros/contas-a-pagar/buscar"""
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(
            f"{CA_API}/v1/financeiro/eventos-financeiros/contas-a-pagar/buscar",
            headers=await _headers(),
            params={
                "descricao": descricao,
                "data_vencimento_de": data_venc_de,
                "data_vencimento_ate": data_venc_ate,
                "status": ["ATRASADO", "EM_ABERTO"],
                "pagina": 1,
                "tamanho_pagina": 5,
            },
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("itens", [])


async def buscar_parcelas_abertas_pagar(conta_financeira_id: str, data_venc_de: str, data_venc_ate: str,
                                         pagina: int = 1, tamanho: int = 50) -> tuple[list, int]:
    """GET /v1/financeiro/eventos-financeiros/contas-a-pagar/buscar - parcelas abertas filtradas por conta."""
    resp = await _request_with_retry(
        "get", f"{CA_API}/v1/financeiro/eventos-financeiros/contas-a-pagar/buscar",
        headers=await _headers(),
        params={
            "data_vencimento_de": data_venc_de,
            "data_vencimento_ate": data_venc_ate,
            "status": ["ATRASADO", "EM_ABERTO"],
            "ids_contas_financeiras": [conta_financeira_id],
            "pagina": pagina,
            "tamanho_pagina": tamanho,
        },
    )
    data = resp.json()
    return data.get("itens", []), data.get("itens_totais", 0)


async def buscar_parcelas_abertas_receber(conta_financeira_id: str, data_venc_de: str, data_venc_ate: str,
                                           pagina: int = 1, tamanho: int = 50) -> tuple[list, int]:
    """GET /v1/financeiro/eventos-financeiros/contas-a-receber/buscar - parcelas abertas filtradas por conta."""
    resp = await _request_with_retry(
        "get", f"{CA_API}/v1/financeiro/eventos-financeiros/contas-a-receber/buscar",
        headers=await _headers(),
        params={
            "data_vencimento_de": data_venc_de,
            "data_vencimento_ate": data_venc_ate,
            "status": ["ATRASADO", "EM_ABERTO"],
            "ids_contas_financeiras": [conta_financeira_id],
            "pagina": pagina,
            "tamanho_pagina": tamanho,
        },
    )
    data = resp.json()
    return data.get("itens", []), data.get("itens_totais", 0)


async def listar_contas_financeiras() -> list:
    """GET /v1/conta-financeira — list all financial accounts (paginated)."""
    items = []
    page = 1
    while True:
        resp = await _request_with_retry(
            "get", f"{CA_API}/v1/conta-financeira",
            headers=await _headers(),
            params={"pagina": page, "tamanho_pagina": 50},
        )
        data = resp.json()
        batch = data.get("itens", data if isinstance(data, list) else [])
        if not batch:
            break
        items.extend(batch)
        total = data.get("itens_totais", len(items))
        if len(items) >= total:
            break
        page += 1
    return items


async def listar_centros_custo() -> list:
    """GET /v1/centro-de-custo — list all cost centers (paginated)."""
    items = []
    page = 1
    while True:
        resp = await _request_with_retry(
            "get", f"{CA_API}/v1/centro-de-custo",
            headers=await _headers(),
            params={"pagina": page, "tamanho_pagina": 50},
        )
        data = resp.json()
        batch = data.get("itens", data if isinstance(data, list) else [])
        if not batch:
            break
        items.extend(batch)
        total = data.get("itens_totais", len(items))
        if len(items) >= total:
            break
        page += 1
    return items


async def criar_baixa(parcela_id: str, data_pagamento: str, valor: float, conta_financeira: str) -> dict:
    """POST /v1/financeiro/eventos-financeiros/parcelas/{id}/baixa"""
    payload = {
        "data_pagamento": data_pagamento,
        "composicao_valor": {
            "valor_bruto": valor,
        },
        "conta_financeira": conta_financeira,
    }
    resp = await _request_with_retry(
        "post", f"{CA_API}/v1/financeiro/eventos-financeiros/parcelas/{parcela_id}/baixa",
        headers=await _headers(), json=payload,
    )
    return resp.json()
