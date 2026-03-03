"""
Admin authentication endpoints (login).
"""
import secrets
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.config import settings
from ._deps import _sessions

router = APIRouter()


# ── Auth ──────────────────────────────────────────────────────

class LoginRequest(BaseModel):
    password: str


@router.post("/login")
async def login(req: LoginRequest):
    """Authenticate with admin password. Returns session token."""
    if not settings.admin_password:
        raise HTTPException(status_code=500, detail="ADMIN_PASSWORD not configured")

    if req.password != settings.admin_password:
        raise HTTPException(status_code=401, detail="Invalid password")

    token = secrets.token_urlsafe(32)
    _sessions[token] = datetime.now(timezone.utc)
    return {"token": token}
