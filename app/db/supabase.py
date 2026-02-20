import base64
import json
import logging

from supabase import create_client, Client

from app.config import settings

_client: Client | None = None
_role_checked = False

logger = logging.getLogger(__name__)


def _decode_jwt_role(token: str) -> str | None:
    parts = token.split(".")
    if len(parts) != 3:
        return None

    try:
        payload = parts[1]
        padding = "=" * (-len(payload) % 4)
        data = base64.urlsafe_b64decode(f"{payload}{padding}").decode("utf-8")
        parsed = json.loads(data)
        role = parsed.get("role")
        return role if isinstance(role, str) else None
    except Exception:
        return None


def _is_service_role_key(key: str) -> bool:
    # New Supabase secret keys (recommended)
    if key.startswith("sb_secret_"):
        return True
    # Publishable keys should never be used by backend write paths
    if key.startswith("sb_publishable_") or key.startswith("sbp_"):
        return False

    # Legacy JWT keys
    role = _decode_jwt_role(key)
    return role == "service_role"


def _effective_key() -> str:
    return settings.supabase_service_role_key or settings.supabase_key


def get_db() -> Client:
    global _client, _role_checked
    if _client is None:
        key = _effective_key()
        if not _role_checked:
            if not _is_service_role_key(key):
                logger.critical(
                    "Supabase backend key is not service-role. "
                    "Background sync and admin writes may fail under RLS. "
                    "Configure SUPABASE_SERVICE_ROLE_KEY."
                )
            _role_checked = True
        _client = create_client(settings.supabase_url, key)
    return _client
