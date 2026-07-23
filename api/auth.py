from __future__ import annotations
import logging
from fastapi import APIRouter, HTTPException, Header, Response, Request
from pydantic import BaseModel

from database.session import async_session
from database.models import Setting
from core.auth import hash_password, verify_password, session_store, get_token_from_request
from core.config import SESSION_TTL

logger = logging.getLogger(__name__)

router = APIRouter()


class LoginRequest(BaseModel):
    password: str


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str


async def _get_stored_password() -> tuple[str, str] | None:
    """Returns (salt, hash) from the settings table, or None if not set."""
    async with async_session() as session:
        salt_row = await session.get(Setting, "admin_password_salt")
        hash_row = await session.get(Setting, "admin_password_hash")
        if salt_row and hash_row:
            return salt_row.value, hash_row.value
        return None


async def _set_stored_password(salt: str, hash_value: str) -> None:
    """Upsert salt and hash into the settings table."""
    async with async_session() as session:
        for key, value in [("admin_password_salt", salt), ("admin_password_hash", hash_value)]:
            existing = await session.get(Setting, key)
            if existing:
                existing.value = value
            else:
                session.add(Setting(key=key, value=value))
        await session.commit()


@router.post("/api/auth/login")
async def login(req: LoginRequest, response: Response):
    stored = await _get_stored_password()
    if not stored:
        raise HTTPException(status_code=500, detail="No admin password configured")
    salt, stored_hash = stored
    if not verify_password(req.password, salt, stored_hash):
        raise HTTPException(status_code=401, detail="Invalid password")
    token = session_store.create()
    response.set_cookie(
        key="auth_token",
        value=token,
        httponly=True,
        secure=False,  # HTTP for desktop app; set True if behind HTTPS reverse proxy
        samesite="strict",
        max_age=SESSION_TTL,
    )
    return {"ok": True}


@router.post("/api/auth/logout")
async def logout(request: Request, response: Response):
    token = get_token_from_request(request)
    if token:
        session_store.revoke(token)
    response.delete_cookie(key="auth_token")
    return {"ok": True}


@router.get("/api/auth/check")
async def check_auth(request: Request):
    token = get_token_from_request(request)
    valid = bool(token) and session_store.validate(token)
    return {"authenticated": valid}


@router.post("/api/auth/change-password")
async def change_password(req: ChangePasswordRequest, request: Request):
    token = get_token_from_request(request)
    if not token or not session_store.validate(token):
        raise HTTPException(status_code=401, detail="Authentication required")
    stored = await _get_stored_password()
    if not stored:
        raise HTTPException(status_code=500, detail="No admin password configured")
    salt, stored_hash = stored
    if not verify_password(req.current_password, salt, stored_hash):
        raise HTTPException(status_code=401, detail="Current password is incorrect")
    if len(req.new_password) < 4:
        raise HTTPException(status_code=400, detail="New password must be at least 4 characters")
    new_salt, new_hash = hash_password(req.new_password)
    await _set_stored_password(new_salt, new_hash)
    logger.info("Admin password changed")
    return {"ok": True}
