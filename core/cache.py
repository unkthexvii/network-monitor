from __future__ import annotations
"""
Simple in-memory TTL cache. No external dependencies.
Used to avoid re-querying expensive dashboard/report data
that only changes once per minute (when the aggregator runs).
"""
import time
from typing import Optional
from core.config import CACHE_TTL_SECONDS


class TTLCache:
    """Thread-safe-ish dict-based cache with per-key expiry."""
    
    def __init__(self, ttl: Optional[int] = None):
        self._store: dict = {}
        self._ttl = ttl or CACHE_TTL_SECONDS

    def get(self, key: str):
        """Returns cached value or None if expired/missing."""
        entry = self._store.get(key)
        if entry is None:
            return None
        value, expiry = entry
        if time.monotonic() > expiry:
            del self._store[key]
            return None
        return value

    def set(self, key: str, value):
        """Stores a value with TTL expiry."""
        self._store[key] = (value, time.monotonic() + self._ttl)

    def invalidate(self, key: Optional[str] = None):
        """Clear one key or the entire cache."""
        if key:
            self._store.pop(key, None)
        else:
            self._store.clear()


# Singleton instance for report/heatmap data caching
report_cache = TTLCache()
