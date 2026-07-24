"""Tests for core/auth.py — password hashing and session store."""
import time
from core.auth import hash_password, verify_password, SessionStore


# -- Password Hashing --

def test_hash_password_returns_salt_and_hash():
    salt, h = hash_password("mypassword")
    assert isinstance(salt, str)
    assert isinstance(h, str)
    assert len(salt) == 32  # 16 bytes hex
    assert len(h) == 64     # 32 bytes hex


def test_hash_password_deterministic_with_same_salt():
    salt1, h1 = hash_password("test")
    # hash_password has no __wrapped__ attribute; verify determinism by
    # re-hashing with the *same* salt and checking the result matches.
    from core.auth import _HASH_ALGO, _HASH_ITERATIONS, _HASH_LENGTH
    import hashlib
    h2 = hashlib.pbkdf2_hmac(
        _HASH_ALGO, b"test", salt1.encode(), _HASH_ITERATIONS, dklen=_HASH_LENGTH
    ).hex()
    assert h2 == h1


def test_hash_password_different_salts_differ():
    salt1, h1 = hash_password("test")
    salt2, h2 = hash_password("test")
    assert salt1 != salt2  # random salts should differ
    assert h1 != h2


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


# -- Session Store --

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
