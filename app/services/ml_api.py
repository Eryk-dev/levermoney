"""
Cliente para APIs do Mercado Livre e Mercado Pago.
"""
import httpx
from app.db.supabase import get_db

ML_API = "https://api.mercadolibre.com"
MP_API = "https://api.mercadopago.com"


async def _get_token(seller_slug: str) -> str:
    """Pega access_token do seller. Se expirado, faz refresh."""
    db = get_db()
    seller = db.table("sellers").select("ml_access_token, ml_refresh_token, ml_token_expires_at").eq(
        "slug", seller_slug
    ).single().execute()
    s = seller.data

    from datetime import datetime, timezone
    expires_at = datetime.fromisoformat(s["ml_token_expires_at"]) if s.get("ml_token_expires_at") else None
    if expires_at and expires_at > datetime.now(timezone.utc):
        return s["ml_access_token"]

    # Refresh token
    from app.config import settings
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(f"{MP_API}/oauth/token", json={
            "grant_type": "refresh_token",
            "client_id": settings.ml_app_id,
            "client_secret": settings.ml_secret_key,
            "refresh_token": s["ml_refresh_token"],
        })
        resp.raise_for_status()
        data = resp.json()

    from datetime import timedelta
    new_expires = datetime.now(timezone.utc) + timedelta(seconds=data["expires_in"])
    db.table("sellers").update({
        "ml_access_token": data["access_token"],
        "ml_refresh_token": data["refresh_token"],
        "ml_token_expires_at": new_expires.isoformat(),
    }).eq("slug", seller_slug).execute()

    return data["access_token"]


async def get_payment(seller_slug: str, payment_id: int) -> dict:
    """GET /v1/payments/{id}"""
    token = await _get_token(seller_slug)
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(
            f"{MP_API}/v1/payments/{payment_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        resp.raise_for_status()
        return resp.json()


async def get_order(seller_slug: str, order_id: int) -> dict:
    """GET /orders/{id}"""
    token = await _get_token(seller_slug)
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(
            f"{ML_API}/orders/{order_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        resp.raise_for_status()
        return resp.json()


async def get_shipment_costs(seller_slug: str, shipment_id: int) -> dict:
    """GET /shipments/{id}/costs"""
    token = await _get_token(seller_slug)
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(
            f"{ML_API}/shipments/{shipment_id}/costs",
            headers={"Authorization": f"Bearer {token}"},
        )
        resp.raise_for_status()
        return resp.json()


async def search_payments(seller_slug: str, begin_date: str, end_date: str, offset: int = 0, limit: int = 50) -> dict:
    """GET /v1/payments/search - busca payments por período."""
    token = await _get_token(seller_slug)
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(
            f"{MP_API}/v1/payments/search",
            headers={"Authorization": f"Bearer {token}"},
            params={
                "sort": "date_created",
                "criteria": "asc",
                "range": "date_created",
                "begin_date": begin_date,
                "end_date": end_date,
                "offset": offset,
                "limit": limit,
            },
        )
        resp.raise_for_status()
        return resp.json()


async def exchange_code(code: str) -> dict:
    """Troca authorization_code por access_token + refresh_token."""
    from app.config import settings
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(f"{MP_API}/oauth/token", json={
            "grant_type": "authorization_code",
            "client_id": settings.ml_app_id,
            "client_secret": settings.ml_secret_key,
            "code": code,
            "redirect_uri": settings.ml_redirect_uri,
        })
        resp.raise_for_status()
        return resp.json()
