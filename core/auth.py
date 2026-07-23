from __future__ import annotations
"""
Password-based authentication for the Network Monitoring System.
Single admin password — stdlib only (hashlib + secrets).
No external dependencies.
"""
import hashlib
import secrets
import time
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# === Password Hashing (PBKDF2-HMAC-SHA256) ===

_HASH_ALGO = "sha256"
_HASH_ITERATIONS = 200_000
_HASH_LENGTH = 32
_SALT_LENGTH = 16


def hash_password(password: str) -> tuple[str, str]:
    """Returns (salt_hex, hash_hex)."""
    salt = secrets.token_hex(_SALT_LENGTH)
    h = hashlib.pbkdf2_hmac(_HASH_ALGO, password.encode(), salt.encode(), _HASH_ITERATIONS, dklen=_HASH_LENGTH)
    return salt, h.hex()


def verify_password(password: str, salt: str, stored_hash: str) -> bool:
    """Returns True if the password matches the stored salt+hash."""
    h = hashlib.pbkdf2_hmac(_HASH_ALGO, password.encode(), salt.encode(), _HASH_ITERATIONS, dklen=_HASH_LENGTH)
    return secrets.compare_digest(h.hex(), stored_hash)


# === Session Store (in-memory) ===

from core.config import SESSION_TTL as _SESSION_TTL


class SessionStore:
    """In-memory session store. Survives until the app restarts."""

    def __init__(self, ttl: int = _SESSION_TTL):
        self._store: dict[str, dict] = {}
        self._ttl = ttl

    def create(self) -> str:
        """Generate a new session token and return it."""
        token = secrets.token_hex(32)
        self._store[token] = {"created": time.time()}
        return token

    def validate(self, token: str) -> bool:
        """Return True if token is valid and not expired."""
        session = self._store.get(token)
        if not session:
            return False
        if time.time() - session["created"] > self._ttl:
            del self._store[token]
            return False
        return True

    def revoke(self, token: str) -> None:
        self._store.pop(token, None)

    def cleanup(self) -> int:
        """Remove expired sessions. Returns count removed."""
        now = time.time()
        expired = [t for t, s in self._store.items() if now - s["created"] > self._ttl]
        for t in expired:
            del self._store[t]
        return len(expired)


def get_token_from_request(request) -> Optional[str]:
    """Extract auth token from httpOnly cookie or Authorization header.
    Cookie takes precedence; Authorization header is a fallback for backward compatibility."""
    token = request.cookies.get("auth_token")
    if token:
        return token
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        return auth_header[7:]
    return None


# Singleton
session_store = SessionStore()
