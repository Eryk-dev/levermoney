"""
Admin authentication endpoints (login).
"""
import secrets
from datetime import datetime, timezone

import bcrypt
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.db.supabase import get_db
from ._deps import _get_password_hash, _sessions, _verify_password

router = APIRouter()


# ── Auth ──────────────────────────────────────────────────────

class LoginRequest(BaseModel):
    password: str


@router.post("/login")
async def login(req: LoginRequest):
    """Authenticate with admin password. Returns session token."""
    hashed = _get_password_hash()
    if not hashed:
        # First-time setup: hash and store the provided password
        new_hash = bcrypt.hashpw(req.password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
        db = get_db()
        db.table("admin_config").upsert({"id": 1, "password_hash": new_hash}).execute()
        hashed = new_hash

    if not _verify_password(req.password, hashed):
        raise HTTPException(status_code=401, detail="Invalid password")

    token = secrets.token_urlsafe(32)
    _sessions[token] = datetime.now(timezone.utc)
    return {"token": token}
