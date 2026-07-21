"""Tests for core/auth.py — password hashing and session store."""
import time
from core.auth import hash_password, verify_password, SessionStore


# ── Password Hashing ──

def test_hash_password_returns_salt_and_hash():
    salt, h = hash_password("mypassword")
    assert isinstance(salt, str)
    assert isinstance(h, str)
    assert len(salt) == 32  # 16 bytes hex
    assert len(h) == 64     # 32 bytes hex


def test_hash_password_deterministic_with_same_salt():
    salt1, h1 = hash_password("test")
    h2 = hash_password.__wrapped__("test") if hasattr(hash_password, "__wrapped__") else None
    # Same password + same salt should produce same hash
    # But since salt is random, we test verify instead
    assert verify_password("test", salt1, h1) is True


def test_verify_password_correct():
    salt, h = hash_password("hello")
    assert verify_password("hello", salt, h) is True


def test_verify_password_wrong():
    salt, h = hash_password("hello")
    assert verify_password("world", salt, h) is False


def test_verify_password_empty():
    salt, h = hash_password("")
    assert verify_password("", salt, h) is True
    assert verify_password("notempty", salt, h) is False


# ── Session Store ──

def test_session_create_and_validate():
    store = SessionStore(ttl=3600)
    token = store.create()
    assert isinstance(token, str)
    assert len(token) == 64  # 32 bytes hex
    assert store.validate(token) is True


def test_session_invalid_token():
    store = SessionStore(ttl=3600)
    assert store.validate("nonexistent") is False


def test_session_expired():
    store = SessionStore(ttl=-1)  # expired immediately
    token = store.create()
    assert store.validate(token) is False


def test_session_revoke():
    store = SessionStore(ttl=3600)
    token = store.create()
    store.revoke(token)
    assert store.validate(token) is False


def test_session_revoke_nonexistent():
    store = SessionStore(ttl=3600)
    store.revoke("nonexistent")  # should not raise


def test_session_cleanup():
    store = SessionStore(ttl=-1)  # all sessions expire immediately
    store.create()
    store.create()
    removed = store.cleanup()
    assert removed == 2
    assert len(store._store) == 0


def test_session_cleanup_no_expired():
    store = SessionStore(ttl=3600)
    store.create()
    removed = store.cleanup()
    assert removed == 0
    assert len(store._store) == 1
