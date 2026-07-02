from __future__ import annotations
"""
In-memory ring buffer for ping results.

Replaces the old RawPing database table. Ping results are held in RAM
and flushed every 60 seconds by the aggregator into MinuteStat rows.

Thread-safe via a simple asyncio Lock (single event loop).
"""
import asyncio
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List
import logging

logger = logging.getLogger(__name__)


@dataclass
class PingSample:
    """A single 1-second ping result."""
    status: str          # "ONLINE" or "OFFLINE"
    latency_ms: float
    packet_loss: float


class PingBuffer:
    """
    Per-device circular buffer of PingSample objects.

    Usage:
        buffer = PingBuffer()
        buffer.append(device_id, PingSample(...))
        snapshot = buffer.flush()   # returns and clears all data
    """

    MAX_SAMPLES_PER_DEVICE = 1000  # ~16 min at 1s intervals; prevents unbounded growth if aggregator stalls

    def __init__(self):
        self._data: Dict[int, List[PingSample]] = defaultdict(list)
        self._lock = None

    @property
    def lock(self):
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    async def append(self, device_id: int, sample: PingSample):
        async with self.lock:
            buf = self._data[device_id]
            buf.append(sample)
            # Trim oldest samples if buffer exceeds max capacity.
            if len(buf) > self.MAX_SAMPLES_PER_DEVICE:
                del buf[:len(buf) - self.MAX_SAMPLES_PER_DEVICE]

    async def append_batch(self, batch: Dict[int, PingSample]):
        """Append many samples at once (one per device). Single lock acquisition."""
        async with self.lock:
            for device_id, sample in batch.items():
                buf = self._data[device_id]
                buf.append(sample)
                # Trim oldest samples if buffer exceeds max capacity.
                # Only checked in append_batch (the hot path) — single-sample append is infrequent.
                if len(buf) > self.MAX_SAMPLES_PER_DEVICE:
                    del buf[:len(buf) - self.MAX_SAMPLES_PER_DEVICE]

    async def flush(self) -> Dict[int, List[PingSample]]:
        """
        Atomically swap out all buffered data and return it.
        After this call the internal buffer is empty.
        """
        async with self.lock:
            snapshot = dict(self._data)
            self._data = defaultdict(list)
            return snapshot

    async def clear_device(self, device_id: int):
        """Remove buffered data for a single device (e.g. on delete)."""
        async with self.lock:
            self._data.pop(device_id, None)


# Global singleton — imported by workers.py and scheduler.py
ping_buffer = PingBuffer()
