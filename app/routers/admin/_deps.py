"""
Shared dependencies for admin sub-modules.
Authentication, session management, and syncer reference.
"""
import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import Header, HTTPException

logger = logging.getLogger(__name__)

# In-memory session tokens (simple approach, survives within process lifetime)
_sessions: dict[str, datetime] = {}


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
