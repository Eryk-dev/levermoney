"""
Cliente para APIs do Mercado Livre e Mercado Pago.
Supports per-seller ML app credentials with fallback to global settings.
"""
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

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


async def search_payments(
    seller_slug: str,
    begin_date: str,
    end_date: str,
    offset: int = 0,
    limit: int = 50,
    range_field: str = "date_approved",
) -> dict:
    """GET /v1/payments/search - busca payments por período.

    range_field: date_approved (default), date_last_updated, date_created, money_release_date.
    """
    token = await _get_token(seller_slug)
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(
            f"{MP_API}/v1/payments/search",
            headers={"Authorization": f"Bearer {token}"},
            params={
                "sort": range_field,
                "criteria": "asc",
                "range": range_field,
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


async def create_settlement_report(
    seller_slug: str,
    begin_date: str,
    end_date: str,
    file_format: str = "csv",
) -> dict[str, Any]:
    """POST /v1/account/settlement_report - request report generation."""
    token = await _get_token(seller_slug)
    payload = {
        "begin_date": begin_date,
        "end_date": end_date,
        "format": file_format,
    }
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            f"{MP_API}/v1/account/settlement_report",
            headers={"Authorization": f"Bearer {token}"},
            json=payload,
        )
        # Some MP accounts reject "format" in this endpoint. Retry without it.
        if resp.status_code >= 400 and file_format:
            fallback_payload = {
                "begin_date": begin_date,
                "end_date": end_date,
            }
            resp = await client.post(
                f"{MP_API}/v1/account/settlement_report",
                headers={"Authorization": f"Bearer {token}"},
                json=fallback_payload,
            )
        resp.raise_for_status()
        return resp.json() if resp.content else {}


async def list_settlement_reports(
    seller_slug: str,
    limit: int = 20,
) -> dict[str, Any]:
    """GET /v1/account/settlement_report/list - list generated reports."""
    token = await _get_token(seller_slug)
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(
            f"{MP_API}/v1/account/settlement_report/list",
            headers={"Authorization": f"Bearer {token}"},
            params={"limit": limit},
        )
        resp.raise_for_status()
        return resp.json() if resp.content else {}


async def download_settlement_report(
    seller_slug: str,
    file_name: str,
) -> bytes:
    """GET /v1/account/settlement_report/{file_name} - download report content."""
    token = await _get_token(seller_slug)
    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.get(
            f"{MP_API}/v1/account/settlement_report/{file_name}",
            headers={"Authorization": f"Bearer {token}"},
        )
        resp.raise_for_status()
        return resp.content


# ── Release Report ───────────────────────────────────────────


async def create_release_report(
    seller_slug: str,
    begin_date: str,
    end_date: str,
) -> dict[str, Any]:
    """POST /v1/account/release_report - request report generation."""
    token = await _get_token(seller_slug)

    def _is_date_only(value: str) -> bool:
        if not value:
            return False
        value = value.strip()
        return len(value) >= 10 and value[4] == "-" and value[7] == "-" and "T" not in value

    def _to_utc_z(value: str) -> str | None:
        if not value:
            return None
        raw = value.strip()
        if not raw:
            return None
        try:
            if raw.endswith("Z"):
                raw = raw.replace("Z", "+00:00")
            dt = datetime.fromisoformat(raw)
            return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        except ValueError:
            return None

    payloads: list[dict[str, str]] = [{
        "begin_date": begin_date,
        "end_date": end_date,
    }]

    # Some accounts require explicit UTC datetimes with Z suffix.
    # If caller passes YYYY-MM-DD, add a fallback payload in UTC window
    # equivalent to the BRT day range.
    if _is_date_only(begin_date) and _is_date_only(end_date):
        begin_day = begin_date[:10]
        end_day = end_date[:10]
        try:
            end_plus_one = (datetime.strptime(end_day, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
            payloads.append({
                "begin_date": f"{begin_day}T03:00:00Z",
                "end_date": f"{end_plus_one}T02:59:59Z",
            })
        except ValueError:
            pass

    # If caller passes datetime with timezone (e.g. -03:00), add a UTC Z variant.
    begin_z = _to_utc_z(begin_date)
    end_z = _to_utc_z(end_date)
    if begin_z and end_z:
        payloads.append({
            "begin_date": begin_z,
            "end_date": end_z,
        })

    # Deduplicate payload variants preserving order.
    unique_payloads: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for payload in payloads:
        key = (payload.get("begin_date", ""), payload.get("end_date", ""))
        if key in seen:
            continue
        seen.add(key)
        unique_payloads.append(payload)

    last_resp = None
    async with httpx.AsyncClient(timeout=30.0) as client:
        for payload in unique_payloads:
            resp = await client.post(
                f"{MP_API}/v1/account/release_report",
                headers={"Authorization": f"Bearer {token}"},
                json=payload,
            )
            if resp.status_code < 400:
                return resp.json() if resp.content else {}
            last_resp = resp
            logger.warning(
                "[%s] create_release_report failed (%s): %s | payload=%s",
                seller_slug,
                resp.status_code,
                (resp.text or "")[:300],
                payload,
            )

    if last_resp is not None:
        last_resp.raise_for_status()
    return {}


async def list_release_reports(
    seller_slug: str,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """GET /v1/account/release_report/list - list generated reports."""
    token = await _get_token(seller_slug)
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(
            f"{MP_API}/v1/account/release_report/list",
            headers={"Authorization": f"Bearer {token}"},
            params={"limit": limit},
        )
        resp.raise_for_status()
        data = resp.json() if resp.content else []
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            for key in ("results", "reports", "data", "items", "files"):
                value = data.get(key)
                if isinstance(value, list):
                    return value
        return []


async def download_release_report(
    seller_slug: str,
    file_name: str,
) -> bytes:
    """GET /v1/account/release_report/{file_name} - download report content."""
    token = await _get_token(seller_slug)
    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.get(
            f"{MP_API}/v1/account/release_report/{file_name}",
            headers={"Authorization": f"Bearer {token}"},
        )
        resp.raise_for_status()
        return resp.content
