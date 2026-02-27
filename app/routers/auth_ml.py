"""
OAuth2 flow para Mercado Livre.
/auth/ml/connect?seller=xxx → redirect para ML
/auth/ml/callback → troca code por token, salva no Supabase
/auth/ml/install → self-service install flow for new sellers
"""
import logging
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse

from app.config import settings
from app.db.supabase import get_db
from app.services.ml_api import exchange_code, fetch_user_info

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth/ml")

_NEW_INSTALL_STATE = "_new_install"


@router.get("/connect")
async def connect(seller: str):
    """
    Redireciona o seller para autorizar no ML.
    Uso: GET /auth/ml/connect?seller=141air
    Works for any existing seller — allows reconnection after token revocation,
    disconnect, or suspension. Only truly rejected sellers are blocked.
    """
    db = get_db()
    existing = db.table("sellers").select("slug, onboarding_status").eq("slug", seller).execute()
    if not existing.data:
        raise HTTPException(status_code=404, detail=f"Seller '{seller}' not found in database")

    params = urlencode({
        "response_type": "code",
        "client_id": settings.ml_app_id,
        "redirect_uri": settings.ml_redirect_uri,
        "state": seller,
    })
    return RedirectResponse(f"https://auth.mercadolivre.com.br/authorization?{params}")


@router.get("/install")
async def install():
    """Self-service: redirect to ML OAuth without requiring a pre-existing seller."""
    params = urlencode({
        "response_type": "code",
        "client_id": settings.ml_app_id,
        "redirect_uri": settings.ml_redirect_uri,
        "state": _NEW_INSTALL_STATE,
    })
    return RedirectResponse(f"https://auth.mercadolivre.com.br/authorization?{params}")


@router.get("/callback")
async def callback(code: str, state: str = ""):
    """
    Callback do OAuth ML. Troca code por tokens e salva no Supabase.
    If state == _new_install: auto-creates seller from ML profile (pending_approval).
    Otherwise: existing flow — saves tokens and activates seller.
    """
    if not state:
        raise HTTPException(status_code=400, detail="Missing state (seller slug)")

    try:
        token_data = await exchange_code(code)
    except Exception as e:
        logger.error(f"OAuth exchange failed for {state}: {e}")
        raise HTTPException(status_code=502, detail=f"ML OAuth failed: {e}")

    expires_at = datetime.now(timezone.utc) + timedelta(seconds=token_data["expires_in"])

    # --- Self-service install flow ---
    if state == _NEW_INSTALL_STATE:
        return await _handle_new_install(token_data, expires_at)

    # --- Existing seller flow (connect / reconnect) ---
    seller_slug = state
    db = get_db()

    # Check seller exists and get current state
    existing = db.table("sellers").select("slug, onboarding_status, approved_at").eq("slug", seller_slug).execute()
    if not existing.data:
        raise HTTPException(status_code=404, detail=f"Seller '{seller_slug}' not found")

    seller = existing.data[0]
    old_status = seller.get("onboarding_status")

    # Update tokens and reactivate
    update_data = {
        "ml_user_id": token_data.get("user_id"),
        "ml_access_token": token_data["access_token"],
        "ml_refresh_token": token_data["refresh_token"],
        "ml_token_expires_at": expires_at.isoformat(),
        "active": True,
    }
    # If seller was previously approved, auto-reactivate to 'active'
    if seller.get("approved_at") and old_status != "active":
        update_data["onboarding_status"] = "active"

    db.table("sellers").update(update_data).eq("slug", seller_slug).execute()

    # Only call activate_seller if seller was already approved (has approved_at)
    if seller.get("approved_at"):
        from app.services.onboarding import activate_seller
        await activate_seller(seller_slug)

    reconnected = old_status not in (None, "approved", "active")
    logger.info(
        "OAuth success for %s (ml_user_id=%s, old_status=%s, reconnected=%s)",
        seller_slug, token_data.get("user_id"), old_status, reconnected,
    )

    return {
        "status": "success",
        "seller": seller_slug,
        "ml_user_id": token_data.get("user_id"),
        "reconnected": reconnected,
        "message": f"Seller {seller_slug} connected! Token expires at {expires_at.isoformat()}",
    }


async def _handle_new_install(token_data: dict, expires_at: datetime) -> HTMLResponse:
    """Handle self-service install: create seller from ML profile."""
    try:
        user_info = await fetch_user_info(token_data["access_token"])
    except Exception as e:
        logger.error(f"Failed to fetch ML user info: {e}")
        raise HTTPException(status_code=502, detail=f"Failed to fetch ML user info: {e}")

    ml_user_id = user_info["id"]
    nickname = user_info.get("nickname", f"seller_{ml_user_id}")
    slug = nickname.lower().replace(" ", "-")

    # Check if seller already exists (by ml_user_id or slug)
    db = get_db()
    existing = db.table("sellers").select("slug, onboarding_status, approved_at").or_(
        f"ml_user_id.eq.{ml_user_id},slug.eq.{slug}"
    ).execute()

    if existing.data:
        seller = existing.data[0]
        old_status = seller.get("onboarding_status")

        update_data = {
            "ml_user_id": ml_user_id,
            "ml_access_token": token_data["access_token"],
            "ml_refresh_token": token_data["refresh_token"],
            "ml_token_expires_at": expires_at.isoformat(),
            "active": True,
        }
        # If seller was previously approved, auto-reactivate
        if seller.get("approved_at"):
            update_data["onboarding_status"] = "active"

        db.table("sellers").update(update_data).eq("slug", seller["slug"]).execute()
        logger.info(
            "Install: existing seller %s re-authenticated (old_status=%s → %s)",
            seller["slug"], old_status, update_data.get("onboarding_status", old_status),
        )
        return _success_page(seller["slug"], already_exists=True)

    # Create new seller
    from app.services.onboarding import create_signup
    new_seller = await create_signup(slug=slug, name=nickname)

    # Save ML tokens
    db.table("sellers").update({
        "ml_user_id": ml_user_id,
        "ml_access_token": token_data["access_token"],
        "ml_refresh_token": token_data["refresh_token"],
        "ml_token_expires_at": expires_at.isoformat(),
    }).eq("slug", slug).execute()

    logger.info(f"Install: new seller created — slug={slug}, ml_user_id={ml_user_id}")
    return _success_page(slug, already_exists=False)


def _success_page(slug: str, already_exists: bool) -> HTMLResponse:
    """Return a simple success HTML page after install."""
    if already_exists:
        message = f"Sua conta <strong>{slug}</strong> j&aacute; estava cadastrada. Tokens atualizados com sucesso."
    else:
        message = f"Conta <strong>{slug}</strong> criada com sucesso! Aguarde a aprova&ccedil;&atilde;o do administrador para iniciar a sincroniza&ccedil;&atilde;o."

    html = f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Lever Money — Instalação</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #0f172a; color: #e2e8f0; min-height: 100vh; display: flex; align-items: center; justify-content: center; }}
        .card {{ background: #1e293b; border-radius: 16px; padding: 48px; max-width: 480px; text-align: center; box-shadow: 0 25px 50px rgba(0,0,0,0.3); }}
        .icon {{ font-size: 48px; margin-bottom: 16px; }}
        h1 {{ font-size: 24px; margin-bottom: 16px; color: #f8fafc; }}
        p {{ font-size: 16px; line-height: 1.6; color: #94a3b8; }}
        p strong {{ color: #38bdf8; }}
        .back {{ display: inline-block; margin-top: 24px; color: #38bdf8; text-decoration: none; font-size: 14px; }}
        .back:hover {{ text-decoration: underline; }}
    </style>
</head>
<body>
    <div class="card">
        <div class="icon">&#10003;</div>
        <h1>Instalação concluída!</h1>
        <p>{message}</p>
        <a href="/install" class="back">&larr; Voltar</a>
    </div>
</body>
</html>"""
    return HTMLResponse(content=html)
