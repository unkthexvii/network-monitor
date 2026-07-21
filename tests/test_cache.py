"""Tests for core/cache.py — TTLCache with max-size eviction."""
import time
from core.cache import TTLCache


def test_get_missing_key_returns_none():
    cache = TTLCache(ttl=60)
    assert cache.get("nonexistent") is None


def test_set_and_get():
    cache = TTLCache(ttl=60)
    cache.set("key1", "value1")
    assert cache.get("key1") == "value1"


def test_expired_key_returns_none():
    cache = TTLCache(ttl=-1)  # expires immediately (expiry in the past)
    cache.set("key1", "value1")
    assert cache.get("key1") is None


def test_invalidate_single_key():
    cache = TTLCache(ttl=60)
    cache.set("key1", "value1")
    cache.set("key2", "value2")
    cache.invalidate("key1")
    assert cache.get("key1") is None
    assert cache.get("key2") == "value2"


def test_invalidate_all():
    cache = TTLCache(ttl=60)
    cache.set("key1", "value1")
    cache.set("key2", "value2")
    cache.invalidate()
    assert cache.get("key1") is None
    assert cache.get("key2") is None


def test_max_size_eviction():
    cache = TTLCache(ttl=60, max_size=3)
    cache.set("a", 1)
    cache.set("b", 2)
    cache.set("c", 3)
    # Cache is full, next insert should evict
    cache.set("d", 4)
    assert len(cache._store) <= 3
    # The oldest entry should be gone
    assert cache.get("d") == 4


def test_max_size_evicts_expired_first():
    cache = TTLCache(ttl=0, max_size=2)  # expires immediately
    cache.set("a", 1)
    time.sleep(0.01)
    cache.set("b", 2)
    time.sleep(0.01)
    # Both expired, inserting c should evict both expired then c stays
    cache.set("c", 3)
    assert cache.get("c") == 3
    assert len(cache._store) <= 2


def test_max_size_evicts_oldest_when_no_expired():
    cache = TTLCache(ttl=60, max_size=2)
    cache.set("a", 1)
    cache.set("b", 2)
    cache.set("c", 3)  # evicts "a" (oldest by expiry)
    assert cache.get("a") is None
    assert cache.get("b") == 2
    assert cache.get("c") == 3


def test_overwrite_key_does_not_grow():
    cache = TTLCache(ttl=60, max_size=2)
    cache.set("a", 1)
    cache.set("a", 2)
    assert cache.get("a") == 2
    assert len(cache._store) == 1
