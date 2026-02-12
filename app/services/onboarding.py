"""
Onboarding service - orchestrates seller signup -> approve -> activate flow.
"""
import logging
from datetime import datetime, timezone

from app.db.supabase import get_db

logger = logging.getLogger(__name__)


async def create_signup(slug: str, name: str, email: str | None = None) -> dict:
    """Create a new seller with pending_approval status."""
    db = get_db()
    result = db.table("sellers").insert({
        "slug": slug.lower().strip(),
        "name": name.strip(),
        "email": email,
        "active": False,
        "onboarding_status": "pending_approval",
        "source": "ml",
    }).execute()
    logger.info("Seller signup created: %s", slug)
    return result.data[0]


async def approve_seller(seller_id: str, config: dict) -> dict:
    """Approve a pending seller with full config.
    config: {dashboard_empresa, dashboard_grupo, dashboard_segmento, ca_conta_bancaria,
             ca_centro_custo_variavel, ca_contato_ml, ml_app_id, ml_secret_key}
    Also creates a revenue_line and 12 empty goals."""
    db = get_db()

    update_data = {
        "onboarding_status": "approved",
        "approved_at": datetime.now(timezone.utc).isoformat(),
    }
    # Map config fields to seller columns
    for field in [
        "dashboard_empresa", "dashboard_grupo", "dashboard_segmento",
        "ca_conta_bancaria", "ca_centro_custo_variavel",
        "ca_contato_ml", "ml_app_id", "ml_secret_key",
    ]:
        if field in config and config[field] is not None:
            update_data[field] = config[field]

    # Auto-create CA contact if not provided
    if not update_data.get("ca_contato_ml"):
        try:
            from app.services.ca_api import buscar_ou_criar_pessoa
            # Fetch seller to get name for contact
            seller_row = db.table("sellers").select("slug, name, dashboard_empresa").eq("id", seller_id).single().execute()
            s = seller_row.data or {}
            contact_name = f"ML - {update_data.get('dashboard_empresa') or s.get('dashboard_empresa') or s.get('name', seller_id)}"
            slug = s.get("slug", seller_id)[:20]
            contact_id = await buscar_ou_criar_pessoa(
                contact_name, slug,
                f"Auto-created by Lever Money for seller {slug}",
            )
            update_data["ca_contato_ml"] = contact_id
            logger.info("Auto-created CA contact '%s' (%s) for seller %s", contact_name, contact_id, seller_id)
        except Exception as e:
            logger.error("Failed to auto-create CA contact for seller %s: %s", seller_id, e)

    result = db.table("sellers").update(update_data).eq("id", seller_id).execute()
    seller = result.data[0] if result.data else None

    if not seller:
        raise ValueError(f"Seller {seller_id} not found")

    empresa = seller.get("dashboard_empresa")
    grupo = seller.get("dashboard_grupo", "OUTROS")
    segmento = seller.get("dashboard_segmento", "OUTROS")

    # Create revenue line
    if empresa:
        db.table("revenue_lines").upsert({
            "empresa": empresa,
            "grupo": grupo,
            "segmento": segmento,
            "seller_id": seller_id,
            "source": seller.get("source", "ml"),
            "active": True,
        }, on_conflict="empresa").execute()

        # Create 12 goals with valor=0 for current year
        year = datetime.now().year
        goals = [
            {"empresa": empresa, "grupo": grupo, "year": year, "month": m, "valor": 0}
            for m in range(1, 13)
        ]
        db.table("goals").upsert(goals, on_conflict="empresa,year,month").execute()

    # Auto-activate if seller already has ML tokens from install flow
    if seller.get("ml_access_token"):
        await activate_seller(seller["slug"])
        logger.info("Seller auto-activated: %s (had ML tokens)", seller_id)

    logger.info("Seller approved: %s -> empresa=%s", seller_id, empresa)
    return seller


async def reject_seller(seller_id: str) -> dict:
    """Reject a pending seller."""
    db = get_db()
    result = db.table("sellers").update({
        "onboarding_status": "suspended",
    }).eq("id", seller_id).execute()
    logger.info("Seller rejected: %s", seller_id)
    return result.data[0] if result.data else {}


async def activate_seller(slug: str):
    """Mark seller as active after successful ML OAuth.
    Called from auth_ml.callback."""
    db = get_db()
    db.table("sellers").update({
        "onboarding_status": "active",
        "active": True,
    }).eq("slug", slug).execute()
    logger.info("Seller activated: %s", slug)
