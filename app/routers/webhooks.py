"""
Webhook receiver para Mercado Livre / Mercado Pago.
Requisito: responder <500ms, processar async.
"""
import hashlib
import hmac
import logging
from fastapi import APIRouter, Request, BackgroundTasks, HTTPException

from app.config import settings
from app.db.supabase import get_db
from app.models.sellers import get_seller_config, get_seller_by_ml_user_id
from app.services.processor import process_payment_webhook

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/webhooks")


def _validate_mp_signature(request: Request, body: bytes, seller_secret: str) -> bool:
    """
    Valida assinatura HMAC-SHA256 do Mercado Pago.
    Header x-signature: ts=xxx,v1=xxx
    Template: id:{data.id};request-id:{x-request-id};ts:{ts};
    """
    x_signature = request.headers.get("x-signature", "")
    x_request_id = request.headers.get("x-request-id", "")

    if not x_signature:
        return True  # ML webhooks (sem assinatura) - aceitar no MVP

    parts = {}
    for part in x_signature.split(","):
        if "=" in part:
            k, v = part.split("=", 1)
            parts[k.strip()] = v.strip()

    ts = parts.get("ts", "")
    v1 = parts.get("v1", "")
    if not ts or not v1:
        return True  # Formato inesperado, aceitar no MVP

    import json
    try:
        data = json.loads(body)
        data_id = str(data.get("data", {}).get("id", ""))
    except Exception:
        return False

    manifest = f"id:{data_id};request-id:{x_request_id};ts:{ts};"
    calculated = hmac.new(
        seller_secret.encode(), manifest.encode(), hashlib.sha256
    ).hexdigest()

    return hmac.compare_digest(calculated, v1)


@router.post("/ml")
async def receive_webhook_ml(
    request: Request,
    background_tasks: BackgroundTasks,
):
    """
    Endpoint global de webhooks ML/MP.
    URL configurada no app ML: https://app.levermoney.com.br/webhooks/ml
    Identifica o seller pelo user_id do payload.
    """
    body = await request.body()

    # Validar assinatura (MVP: log warning mas aceita)
    if not _validate_mp_signature(request, body, settings.ml_secret_key):
        logger.warning("Invalid webhook signature")

    # Parse body
    import json
    try:
        payload = json.loads(body)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    # Extrair dados do webhook
    topic = payload.get("topic") or payload.get("type", "unknown")
    resource = payload.get("resource") or ""
    data_id = payload.get("data", {}).get("id")
    action = payload.get("action", "")
    user_id = payload.get("user_id")

    # Identificar seller pelo user_id do ML
    db = get_db()
    seller = None
    seller_slug = "unknown"

    if user_id:
        seller = get_seller_by_ml_user_id(db, int(user_id))
        if seller:
            seller_slug = seller["slug"]

    logger.info(f"Webhook ML: seller={seller_slug} topic={topic} action={action} data_id={data_id} user_id={user_id}")

    # Salvar raw event (sempre, mesmo sem seller identificado)
    db.table("webhook_events").insert({
        "seller_slug": seller_slug,
        "topic": topic,
        "action": action,
        "resource": resource,
        "data_id": str(data_id) if data_id else None,
        "raw_payload": payload,
        "status": "received" if seller else "unmatched",
    }).execute()

    if not seller:
        logger.warning(f"No seller found for user_id={user_id}")
        return {"status": "ok"}

    # Processar em background por topic
    if topic == "payment" and data_id:
        background_tasks.add_task(process_payment_webhook, seller_slug, int(data_id))

    # Responder rápido
    return {"status": "ok"}
