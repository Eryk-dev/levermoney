"""
OAuth2 flow para Mercado Livre.
/auth/ml/connect?seller=xxx → redirect para ML
/auth/ml/callback → troca code por token, salva no Supabase
"""
import logging
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException
from fastapi.responses import RedirectResponse

from app.config import settings
from app.db.supabase import get_db
from app.services.ml_api import exchange_code

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth/ml")


@router.get("/connect")
async def connect(seller: str):
    """
    Redireciona o seller para autorizar no ML.
    Uso: GET /auth/ml/connect?seller=141air
    Accepts sellers with onboarding_status in ('approved', 'active', None).
    """
    db = get_db()
    existing = db.table("sellers").select("slug, onboarding_status").eq("slug", seller).execute()
    if not existing.data:
        raise HTTPException(status_code=404, detail=f"Seller '{seller}' not found in database")

    status = existing.data[0].get("onboarding_status")
    allowed = (None, "approved", "active")
    if status not in allowed:
        raise HTTPException(status_code=403, detail=f"Seller '{seller}' is not approved (status={status})")

    params = urlencode({
        "response_type": "code",
        "client_id": settings.ml_app_id,
        "redirect_uri": settings.ml_redirect_uri,
        "state": seller,
    })
    return RedirectResponse(f"https://auth.mercadolivre.com.br/authorization?{params}")


@router.get("/callback")
async def callback(code: str, state: str = ""):
    """
    Callback do OAuth ML. Troca code por tokens e salva no Supabase.
    After successful OAuth, activates the seller via onboarding.
    """
    seller_slug = state
    if not seller_slug:
        raise HTTPException(status_code=400, detail="Missing state (seller slug)")

    try:
        token_data = await exchange_code(code)
    except Exception as e:
        logger.error(f"OAuth exchange failed for {seller_slug}: {e}")
        raise HTTPException(status_code=502, detail=f"ML OAuth failed: {e}")

    expires_at = datetime.now(timezone.utc) + timedelta(seconds=token_data["expires_in"])

    db = get_db()
    db.table("sellers").update({
        "ml_user_id": token_data.get("user_id"),
        "ml_access_token": token_data["access_token"],
        "ml_refresh_token": token_data["refresh_token"],
        "ml_token_expires_at": expires_at.isoformat(),
        "active": True,
    }).eq("slug", seller_slug).execute()

    # Activate seller via onboarding service
    from app.services.onboarding import activate_seller
    await activate_seller(seller_slug)

    logger.info(f"OAuth success for {seller_slug}, ml_user_id={token_data.get('user_id')}")

    return {
        "status": "success",
        "seller": seller_slug,
        "ml_user_id": token_data.get("user_id"),
        "message": f"Seller {seller_slug} connected! Token expires at {expires_at.isoformat()}",
    }
