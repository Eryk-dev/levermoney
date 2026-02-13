"""
OAuth2 flow para Conta Azul.
/auth/ca/connect    → redirect para login CA (auth.contaazul.com)
/auth/ca/callback   → troca code por tokens, salva no Supabase
/auth/ca/status     → verifica validade dos tokens atuais
"""
import base64
import logging
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse

from app.config import settings
from app.db.supabase import get_db
from app.services.ca_api import (
    CA_TOKEN_URL,
    _fetch_ca_tokens_row,
    _now_ms,
    _to_epoch_ms,
    _token_cache,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth/ca")

CA_AUTHORIZE_URL = "https://auth.contaazul.com/login"
CA_SCOPES = "openid profile aws.cognito.signin.user.admin"


@router.get("/connect")
async def connect():
    """Redirect to Conta Azul OAuth2 login page."""
    redirect_uri = f"{settings.base_url}/auth/ca/callback"
    params = urlencode({
        "response_type": "code",
        "client_id": settings.ca_client_id,
        "redirect_uri": redirect_uri,
        "scope": CA_SCOPES,
        "state": "ca_connect",
    })
    return RedirectResponse(f"{CA_AUTHORIZE_URL}?{params}")


@router.get("/callback")
async def callback(code: str, state: str = ""):
    """Exchange authorization code for tokens and save to Supabase."""
    redirect_uri = f"{settings.base_url}/auth/ca/callback"
    client_id = settings.ca_client_id
    client_secret = settings.ca_client_secret

    if not client_secret:
        raise HTTPException(status_code=500, detail="CA_CLIENT_SECRET not configured")

    basic = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            CA_TOKEN_URL,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Authorization": f"Basic {basic}",
            },
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
            },
        )

    if resp.status_code >= 400:
        err = resp.text[:500]
        logger.error(f"CA OAuth callback failed: {err}")
        raise HTTPException(status_code=502, detail=f"CA OAuth failed: {err}")

    data = resp.json()
    access_token = data.get("access_token")
    refresh_token = data.get("refresh_token")
    expires_in = int(data.get("expires_in", 3600))

    if not access_token or not refresh_token:
        raise HTTPException(status_code=502, detail="CA OAuth: missing tokens in response")

    # Persist to Supabase
    expires_at_ms = _now_ms() + (expires_in * 1000)
    db = get_db()
    existing = _fetch_ca_tokens_row(db)

    payload = {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "expires_at": expires_at_ms,
    }
    if existing:
        db.table("ca_tokens").update(payload).eq("id", 1).execute()
    else:
        db.table("ca_tokens").upsert({"id": 1, **payload}).execute()

    # Update in-memory cache
    _token_cache["access_token"] = access_token
    _token_cache["expires_at"] = expires_at_ms

    logger.info(f"CA OAuth connected! Token expires in {expires_in}s")

    return HTMLResponse(content=f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Conta Azul — Conectado</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #0f172a; color: #e2e8f0; min-height: 100vh; display: flex; align-items: center; justify-content: center; }}
        .card {{ background: #1e293b; border-radius: 16px; padding: 48px; max-width: 480px; text-align: center; box-shadow: 0 25px 50px rgba(0,0,0,0.3); }}
        h1 {{ font-size: 24px; margin-bottom: 16px; color: #f8fafc; }}
        p {{ font-size: 16px; line-height: 1.6; color: #94a3b8; }}
        .ok {{ color: #22c55e; font-size: 48px; margin-bottom: 16px; }}
    </style>
</head>
<body>
    <div class="card">
        <div class="ok">&#10003;</div>
        <h1>Conta Azul conectada!</h1>
        <p>Tokens salvos com sucesso. O refresh automatico vai manter a conexao ativa.</p>
    </div>
</body>
</html>""")


@router.get("/status")
async def status():
    """Check current CA token status."""
    db = get_db()
    tokens = _fetch_ca_tokens_row(db)

    if not tokens or not tokens.get("access_token"):
        return {
            "connected": False,
            "message": "Nenhum token encontrado. Conecte via /auth/ca/connect",
        }

    now_ms = _now_ms()
    expires_ms = _to_epoch_ms(tokens.get("expires_at"))
    remaining_s = max(0, (expires_ms - now_ms) / 1000)
    has_refresh = bool(tokens.get("refresh_token"))

    return {
        "connected": True,
        "access_token_valid": expires_ms > now_ms,
        "expires_in_seconds": int(remaining_s),
        "has_refresh_token": has_refresh,
        "message": "OK" if remaining_s > 0 else "Access token expirado (refresh automatico vai renovar)",
    }
