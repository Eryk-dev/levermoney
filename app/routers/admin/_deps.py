"""
Shared dependencies for admin sub-modules.
Authentication, session management, and syncer reference.
"""
import logging
import secrets
from datetime import datetime, timezone
from typing import Any

import bcrypt
from fastapi import Header, HTTPException

from app.db.supabase import get_db

logger = logging.getLogger(__name__)

# In-memory session tokens (simple approach, survives within process lifetime)
_sessions: dict[str, datetime] = {}


def _get_password_hash() -> str | None:
    db = get_db()
    result = db.table("admin_config").select("password_hash").eq("id", 1).execute()
    if result.data:
        return result.data[0]["password_hash"]
    return None


def _verify_password(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))


async def require_admin(x_admin_token: str = Header(...)):
    """Dependency: verify admin session token."""
    if x_admin_token not in _sessions:
        raise HTTPException(status_code=401, detail="Invalid or expired admin token")
    # Check expiry (24h sessions)
    created = _sessions[x_admin_token]
    if (datetime.now(timezone.utc) - created).total_seconds() > 86400:
        del _sessions[x_admin_token]
        raise HTTPException(status_code=401, detail="Session expired")
    return True


# syncer reference set by main.py
_syncer: Any = None


def set_syncer(syncer):
    global _syncer
    _syncer = syncer


def get_syncer():
    return _syncer
