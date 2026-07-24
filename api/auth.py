from __future__ import annotations
import logging
import time
from fastapi import APIRouter, HTTPException, Response, Request
from pydantic import BaseModel

from database.session import async_session
from database.models import Setting
from core.auth import hash_password, verify_password, session_store, get_token_from_request
from core.config import SESSION_TTL

logger = logging.getLogger(__name__)

router = APIRouter()


# === Rate Limiter for Failed Login Attempts ===

class FailedLoginRateLimiter:
    """In-memory rate limiter for failed login attempts.

    Tracks: IP address -> {count, first_attempt_time}
    Resets counter after 1 minute.
    Thread-safe for async context (GIL protects dict operations).
    """

    def __init__(self, max_attempts: int = 5, window_seconds: int = 60):
        self._attempts: dict[str, dict] = {}
        self._max_attempts = max_attempts
        self._window_seconds = window_seconds

    def is_blocked(self, ip: str) -> bool:
        """Check if the IP is currently rate limited."""
        self._cleanup(ip)
        record = self._attempts.get(ip)
        if not record:
            return False
        return record["count"] >= self._max_attempts

    def record_failure(self, ip: str) -> None:
        """Record a failed login attempt for the IP."""
        now = time.time()
        if ip not in self._attempts:
            self._attempts[ip] = {"count": 1, "first_attempt_time": now}
        else:
            record = self._attempts[ip]
            # Reset if window has expired
            if now - record["first_attempt_time"] > self._window_seconds:
                record["count"] = 1
                record["first_attempt_time"] = now
            else:
                record["count"] += 1

    def record_success(self, ip: str) -> None:
        """Clear failed attempts for the IP on successful login."""
        self._attempts.pop(ip, None)

    def _cleanup(self, ip: str) -> None:
        """Remove expired records for the given IP."""
        now = time.time()
        if ip in self._attempts:
            if now - self._attempts[ip]["first_attempt_time"] > self._window_seconds:
                del self._attempts[ip]

    def cleanup_expired(self) -> int:
        """Remove all expired records. Returns count removed."""
        now = time.time()
        expired_ips = [
            ip for ip, record in self._attempts.items()
            if now - record["first_attempt_time"] > self._window_seconds
        ]
        for ip in expired_ips:
            del self._attempts[ip]
        return len(expired_ips)


# Singleton rate limiter instance
login_rate_limiter = FailedLoginRateLimiter()


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
async def login(req: LoginRequest, request: Request, response: Response):
    # Rate limiting: 5 failed attempts per IP per minute
    client_ip = request.client.host if request.client else "unknown"
    if login_rate_limiter.is_blocked(client_ip):
        raise HTTPException(
            status_code=429,
            detail="Too many login attempts. Please try again in 60 seconds.",
        )
    stored = await _get_stored_password()
    if not stored:
        raise HTTPException(status_code=500, detail="No admin password configured")
    salt, stored_hash = stored
    if not verify_password(req.password, salt, stored_hash):
        login_rate_limiter.record_failure(client_ip)
        raise HTTPException(status_code=401, detail="Invalid password")
    login_rate_limiter.record_success(client_ip)
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
    if len(req.new_password) < 12:
        raise HTTPException(status_code=400, detail="New password must be at least 12 characters")
    if not any(c.isupper() for c in req.new_password):
        raise HTTPException(status_code=400, detail="New password must contain at least one uppercase letter")
    if not any(c.islower() for c in req.new_password):
        raise HTTPException(status_code=400, detail="New password must contain at least one lowercase letter")
    if not any(c.isdigit() for c in req.new_password):
        raise HTTPException(status_code=400, detail="New password must contain at least one digit")
    if not any(c in "!@#$%^&*()_+-=[]{}|;:,.<>?" for c in req.new_password):
        raise HTTPException(status_code=400, detail="New password must contain at least one special character")
    new_salt, new_hash = hash_password(req.new_password)
    await _set_stored_password(new_salt, new_hash)
    logger.info("Admin password changed")
    return {"ok": True}
