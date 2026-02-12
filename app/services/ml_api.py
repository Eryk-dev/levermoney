"""
Cliente para APIs do Mercado Livre e Mercado Pago.
Supports per-seller ML app credentials with fallback to global settings.
"""
import logging
from datetime import datetime, timedelta, timezone

import httpx

from app.db.supabase import get_db

ML_API = "https://api.mercadolibre.com"
MP_API = "https://api.mercadopago.com"

logger = logging.getLogger(__name__)


def _get_seller_credentials(seller: dict) -> tuple[str, str]:
    """Get ML app_id and secret_key for a seller.
    Uses per-seller credentials if available, falls back to global settings."""
    from app.config import settings
    app_id = seller.get("ml_app_id") or settings.ml_app_id
    secret = seller.get("ml_secret_key") or settings.ml_secret_key
    return app_id, secret


async def _get_token(seller_slug: str) -> str:
    """Pega access_token do seller. Se expirado, faz refresh."""
    db = get_db()
    seller = db.table("sellers").select(
        "ml_access_token, ml_refresh_token, ml_token_expires_at, ml_app_id, ml_secret_key"
    ).eq("slug", seller_slug).single().execute()
    s = seller.data

    expires_at = datetime.fromisoformat(s["ml_token_expires_at"]) if s.get("ml_token_expires_at") else None
    if expires_at and expires_at > datetime.now(timezone.utc):
        return s["ml_access_token"]

    # Refresh token using per-seller or global credentials
    app_id, secret = _get_seller_credentials(s)
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(f"{MP_API}/oauth/token", json={
            "grant_type": "refresh_token",
            "client_id": app_id,
            "client_secret": secret,
            "refresh_token": s["ml_refresh_token"],
        })
        resp.raise_for_status()
        data = resp.json()

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


async def fetch_user_info(access_token: str) -> dict:
    """GET /users/me — returns ML user profile {id, nickname, ...}."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(
            f"{ML_API}/users/me",
            headers={"Authorization": f"Bearer {access_token}"},
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


async def fetch_paid_orders(seller_slug: str, date_str: str) -> dict:
    """Fetch paid orders total for a seller on a given date.
    Used by faturamento_sync. Returns {valor, order_count, fraud_skipped}."""
    token = await _get_token(seller_slug)
    date_from = f"{date_str}T00:00:00.000-03:00"
    date_to = f"{date_str}T23:59:59.999-03:00"

    total_valor = 0.0
    order_count = 0
    fraud_count = 0
    offset = 0
    limit = 50

    async with httpx.AsyncClient(timeout=30.0) as client:
        while True:
            resp = await client.get(
                f"{ML_API}/orders/search",
                params={
                    "seller": (await _get_ml_user_id(seller_slug)),
                    "order.status": "paid",
                    "order.date_created.from": date_from,
                    "order.date_created.to": date_to,
                    "sort": "date_desc",
                    "limit": limit,
                    "offset": offset,
                },
                headers={"Authorization": f"Bearer {token}"},
            )

            if resp.status_code != 200:
                logger.error("[%s] Orders search failed at offset %d: %s", seller_slug, offset, resp.text)
                break

            data = resp.json()
            for order in data.get("results", []):
                if "fraud_risk_detected" in (order.get("tags") or []):
                    fraud_count += 1
                    continue
                total_valor += order.get("paid_amount", 0) or order.get("total_amount", 0)
                order_count += 1

            total_results = data.get("paging", {}).get("total", 0)
            offset += limit
            if offset >= total_results or offset > 500:
                break

    return {
        "valor": round(total_valor, 2),
        "order_count": order_count,
        "fraud_skipped": fraud_count,
    }


async def _get_ml_user_id(seller_slug: str) -> int:
    """Get the ML user_id for a seller from the database."""
    db = get_db()
    result = db.table("sellers").select("ml_user_id").eq("slug", seller_slug).single().execute()
    return result.data["ml_user_id"]
