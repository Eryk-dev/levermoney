"""
Cliente para API do Conta Azul v2.
Base: https://api-v2.contaazul.com
Rate limit: 600 req/min, 10 req/seg

Tokens armazenados no Supabase (tabela ca_tokens).
Auto-refresh via OAuth2 endpoint (auth.contaazul.com) com token rotation.
"""
import asyncio
import base64
import logging
import time
from datetime import datetime, timezone
from typing import Any

import httpx

from app.config import settings
from app.db.supabase import get_db
from app.services.rate_limiter import rate_limiter

logger = logging.getLogger(__name__)

CA_API = "https://api-v2.contaazul.com"
CA_TOKEN_URL = "https://auth.contaazul.com/oauth2/token"

# Cache em memória para evitar query a cada request
_token_cache = {
    "access_token": None,
    "expires_at": 0,
}
# Lock to prevent concurrent Cognito refresh (race condition → 400 errors)
_refresh_lock = asyncio.Lock()


def _now_ms() -> int:
    return int(time.time() * 1000)


def _is_numeric_string(value: str) -> bool:
    value = value.strip()
    if not value:
        return False
    if value.startswith("-"):
        value = value[1:]
    return value.isdigit()


def _to_epoch_ms(value: Any) -> int:
    """Normalize expiry values from Supabase/env to epoch milliseconds."""
    if value is None:
        return 0

    if isinstance(value, bool):
        return 0

    if isinstance(value, datetime):
        dt = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return int(dt.timestamp() * 1000)

    if isinstance(value, (int, float)):
        raw = int(value)
    elif isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return 0
        try:
            raw = int(float(stripped))
        except ValueError:
            try:
                dt = datetime.fromisoformat(stripped.replace("Z", "+00:00"))
            except ValueError:
                logger.warning("Invalid ca_tokens.expires_at format: %r", value)
                return 0
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return int(dt.timestamp() * 1000)
    else:
        logger.warning("Unsupported ca_tokens.expires_at type: %s", type(value).__name__)
        return 0

    # Seconds epoch (10 digits) -> ms
    if raw < 10_000_000_000:
        return raw * 1000
    # Microseconds epoch (16+ digits) -> ms
    if raw > 10_000_000_000_000:
        return raw // 1000
    return raw


def _epoch_ms_to_iso(value_ms: int) -> str:
    return datetime.fromtimestamp(value_ms / 1000, tz=timezone.utc).isoformat()


def _expiry_candidates_for_db(expires_at_ms: int, existing_value: Any) -> list[Any]:
    """Generate compatible candidate values for ca_tokens.expires_at."""
    if isinstance(existing_value, datetime):
        preferred = _epoch_ms_to_iso(expires_at_ms)
    elif isinstance(existing_value, str):
        preferred = str(expires_at_ms) if _is_numeric_string(existing_value) else _epoch_ms_to_iso(expires_at_ms)
    else:
        preferred = expires_at_ms

    candidates = [preferred]
    for candidate in (expires_at_ms, str(expires_at_ms), _epoch_ms_to_iso(expires_at_ms)):
        if candidate not in candidates:
            candidates.append(candidate)
    return candidates


def _fetch_ca_tokens_row(db) -> dict | None:
    result = db.table("ca_tokens").select("*").eq("id", 1).limit(1).execute()
    rows = result.data or []
    return rows[0] if rows else None


def _persist_ca_tokens(
    db,
    access_token: str,
    refresh_token: str,
    expires_at_ms: int,
    current_row: dict | None,
) -> None:
    """Persist tokens trying compatible expires_at formats."""
    last_error = None
    for expires_value in _expiry_candidates_for_db(
        expires_at_ms, (current_row or {}).get("expires_at")
    ):
        payload = {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "expires_at": expires_value,
        }
        try:
            if current_row:
                db.table("ca_tokens").update(payload).eq("id", 1).execute()
            else:
                db.table("ca_tokens").upsert({"id": 1, **payload}).execute()
            return
        except Exception as e:
            last_error = e

    raise RuntimeError(f"Could not persist CA tokens in Supabase: {last_error}")


async def _refresh_access_token(refresh_token: str) -> tuple[str, int, str | None]:
    """Refresh CA tokens via OAuth2 endpoint (supports refresh token rotation).

    Returns (access_token, expires_in_seconds, new_refresh_token_or_None).
    """
    client_id = settings.ca_client_id
    client_secret = settings.ca_client_secret
    if not client_secret:
        raise RuntimeError(
            "CA_CLIENT_SECRET não configurado. Adicione ao .env."
        )

    basic = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            CA_TOKEN_URL,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Authorization": f"Basic {basic}",
            },
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
            },
        )

    if resp.status_code >= 400:
        err_message = resp.text[:400]
        try:
            err = resp.json()
            err_message = err.get("error_description") or err.get("error") or err_message
        except Exception:
            pass

        normalized = err_message.lower()
        if "invalid_grant" in normalized or "expired" in normalized:
            raise RuntimeError(
                f"CA refresh token expirado/inválido: {err_message}. "
                "Reconecte via /auth/ca/connect"
            )
        raise RuntimeError(f"CA OAuth2 refresh failed ({resp.status_code}): {err_message}")

    data = resp.json()
    new_access_token = data.get("access_token")
    expires_in = int(data.get("expires_in", 3600))
    new_refresh_token = data.get("refresh_token")  # rotation: new token returned

    if not new_access_token:
        raise RuntimeError(f"CA OAuth2 refresh: missing access_token ({data})")

    if new_refresh_token and new_refresh_token != refresh_token:
        logger.info("CA refresh token rotated (new token received)")

    return new_access_token, expires_in, new_refresh_token


async def _get_ca_token() -> str:
    """Pega access_token do CA. Se expirado, faz refresh via OAuth2.
    Uses asyncio.Lock to prevent concurrent refresh attempts.
    Handles refresh token rotation (stores new refresh token each time)."""
    now_ms = _now_ms()

    # Fast path: cache still valid (60s margin)
    if _token_cache["access_token"] and _token_cache["expires_at"] > now_ms + 60000:
        return _token_cache["access_token"]

    async with _refresh_lock:
        # Re-check after acquiring lock (another coroutine may have refreshed)
        now_ms = _now_ms()
        if _token_cache["access_token"] and _token_cache["expires_at"] > now_ms + 60000:
            return _token_cache["access_token"]

        db = get_db()
        tokens = _fetch_ca_tokens_row(db)

        # If Supabase token still valid (another process may have refreshed)
        if tokens and tokens.get("access_token"):
            db_expires_ms = _to_epoch_ms(tokens.get("expires_at"))
            if db_expires_ms > now_ms + 60000:
                _token_cache["access_token"] = tokens["access_token"]
                _token_cache["expires_at"] = db_expires_ms
                return tokens["access_token"]

        env_refresh_token = settings.ca_refresh_token.strip()
        refresh_token = (tokens or {}).get("refresh_token") or env_refresh_token
        if not refresh_token:
            raise RuntimeError(
                "Conta Azul sem refresh token. Reconecte via /auth/ca/connect"
            )

        # Refresh via OAuth2 endpoint (supports token rotation)
        logger.info("CA token expired/missing, refreshing via OAuth2...")
        try:
            new_access_token, expires_in, new_refresh_token = await _refresh_access_token(refresh_token)
        except Exception as e:
            # If DB token became stale, allow env token as recovery path.
            if env_refresh_token and env_refresh_token != refresh_token:
                logger.warning(
                    "Stored CA refresh_token failed; trying CA_REFRESH_TOKEN from environment."
                )
                new_access_token, expires_in, new_refresh_token = await _refresh_access_token(env_refresh_token)
            else:
                raise RuntimeError(f"Failed to refresh Conta Azul token: {e}") from e

        # Use rotated refresh token if returned, otherwise keep current
        final_refresh_token = new_refresh_token or refresh_token
        new_expires_at = _now_ms() + (expires_in * 1000)

        # Persist both access + rotated refresh token to Supabase
        _persist_ca_tokens(
            db=db,
            access_token=new_access_token,
            refresh_token=final_refresh_token,
            expires_at_ms=new_expires_at,
            current_row=tokens,
        )

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
