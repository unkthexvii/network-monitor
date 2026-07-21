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
    """Dict-based cache with per-key expiry and optional max-size eviction."""

    def __init__(self, ttl: Optional[int] = None, max_size: int = 500):
        self._store: dict = {}
        self._ttl = ttl or CACHE_TTL_SECONDS
        self._max_size = max_size

    def _evict_expired(self):
        """Remove all expired entries."""
        now = time.monotonic()
        expired = [k for k, (_, exp) in self._store.items() if now > exp]
        for k in expired:
            del self._store[k]

    def _evict_oldest(self, count: int = 1):
        """Remove the oldest entries by expiry time."""
        if not self._store:
            return
        by_expiry = sorted(self._store.items(), key=lambda kv: kv[1][1])
        for k, _ in by_expiry[:count]:
            del self._store[k]

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
        """Stores a value with TTL eviction. Evicts expired then oldest if over max_size."""
        self._store[key] = (value, time.monotonic() + self._ttl)
        if len(self._store) > self._max_size:
            self._evict_expired()
            if len(self._store) > self._max_size:
                self._evict_oldest(len(self._store) - self._max_size)

    def invalidate(self, key: Optional[str] = None):
        """Clear one key or the entire cache."""
        if key:
            self._store.pop(key, None)
        else:
            self._store.clear()


# Singleton instance for report/heatmap data caching
report_cache = TTLCache()
