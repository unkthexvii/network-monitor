from __future__ import annotations
"""
In-memory cache for the device list and their current status.

Eliminates the need to query the database every 1-second ping cycle.
The cache is refreshed:
  - On startup (full load from DB)
  - Every 30 seconds (light refresh to pick up device adds/removes/edits)
  - Immediately when a device is added, updated, or deleted via the API
"""
import asyncio
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional
from datetime import datetime
import time

logger = logging.getLogger(__name__)


@dataclass
class CachedDevice:
    """Lightweight in-memory representation of a Device + its live status."""
    id: int
    name: str
    ip_address: str
    check_interval: int
    enabled: int
    latency_threshold_ms: float = 200.0
    packet_loss_threshold: float = 0.20
    # Live status fields (mirrors DeviceStatus table)
    status: str = "UNKNOWN"
    latency_ms: float = 0.0
    packet_loss: float = 0.0
    last_seen: Optional[datetime] = None
    offline_since: Optional[datetime] = None
    fail_count: int = 0
    recovery_count: int = 0
    # Flag to track if status needs to be flushed to DB
    dirty: bool = False
    # Concurrency lock to prevent overlapping ping executions for this specific device
    last_ping_start: float = 0.0  # time.monotonic() timestamp; 0 = not currently pinging
    # Track when the current failure streak started (for accurate offline_since calculation)
    first_fail_time: Optional[datetime] = None  # UTC datetime of first failure in current streak


class DeviceCache:
    """
    Thread-safe in-memory device cache.
    Replaces per-second DB queries with O(1) dictionary lookups.
    """

    def __init__(self):
        self._devices: Dict[int, CachedDevice] = {}
        self._lock = None
        self._loaded = False

    @property
    def lock(self):
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    async def load_from_db(self):
        """Full load from database. Called once on startup."""
        from database.session import async_session
        from database.models import Device, DeviceStatus
        from sqlalchemy import select

        async with self.lock:
            async with async_session() as session:
                stmt = select(Device, DeviceStatus).outerjoin(
                    DeviceStatus, Device.id == DeviceStatus.device_id
                )
                result = await session.execute(stmt)

                self._devices.clear()
                for device, status in result:
                    cached = CachedDevice(
                        id=device.id,
                        name=device.name,
                        ip_address=device.ip_address,
                        check_interval=device.check_interval if device.check_interval and device.check_interval > 0 else 60,
                        enabled=device.enabled,
                        latency_threshold_ms=device.latency_threshold_ms or 200.0,
                        packet_loss_threshold=device.packet_loss_threshold or 0.20,
                    )
                    if status:
                        cached.status = status.status or "UNKNOWN"
                        cached.latency_ms = status.latency_ms or 0.0
                        cached.packet_loss = status.packet_loss or 0.0
                        cached.last_seen = status.last_seen
                        cached.offline_since = status.offline_since
                        cached.fail_count = status.fail_count or 0
                        cached.recovery_count = status.recovery_count or 0
                    self._devices[device.id] = cached

                self._loaded = True
                logger.info(f"Device cache loaded: {len(self._devices)} devices")

    async def refresh_from_db(self):
        """
        Light refresh — re-sync device list from DB to pick up
        adds/removes/edits. Called every 30 seconds.
        Preserves live status from memory (not overwritten by DB).
        """
        from database.session import async_session
        from database.models import Device, DeviceStatus
        from sqlalchemy import select

        async with self.lock:
            async with async_session() as session:
                stmt = select(Device, DeviceStatus).outerjoin(
                    DeviceStatus, Device.id == DeviceStatus.device_id
                )
                result = await session.execute(stmt)

                db_ids = set()
                for device, status in result:
                    db_ids.add(device.id)
                    if device.id in self._devices:
                        # Update metadata only, keep live status
                        cached = self._devices[device.id]
                        cached.name = device.name
                        cached.ip_address = device.ip_address
                        cached.check_interval = device.check_interval if device.check_interval and device.check_interval > 0 else 60
                        cached.enabled = device.enabled
                        cached.latency_threshold_ms = device.latency_threshold_ms or 200.0
                        cached.packet_loss_threshold = device.packet_loss_threshold or 0.20
                    else:
                        # New device added
                        cached = CachedDevice(
                            id=device.id,
                            name=device.name,
                            ip_address=device.ip_address,
                            check_interval=device.check_interval if device.check_interval and device.check_interval > 0 else 60,
                            enabled=device.enabled,
                            latency_threshold_ms=device.latency_threshold_ms or 200.0,
                            packet_loss_threshold=device.packet_loss_threshold or 0.20,
                        )
                        if status:
                            cached.status = status.status or "UNKNOWN"
                            cached.fail_count = status.fail_count or 0
                            cached.recovery_count = status.recovery_count or 0
                        self._devices[device.id] = cached

                # Remove devices that no longer exist in DB
                stale_ids = set(self._devices.keys()) - db_ids
                for sid in stale_ids:
                    del self._devices[sid]

    def get_enabled_devices(self) -> List[CachedDevice]:
        """Return all enabled devices (no DB query, pure memory)."""
        return [d for d in self._devices.values() if d.enabled]

    def get_device(self, device_id: int) -> Optional[CachedDevice]:
        return self._devices.get(device_id)

    def get_all(self) -> List[CachedDevice]:
        return list(self._devices.values())

    @property
    def loaded(self) -> bool:
        return self._loaded


# Global singleton
device_cache = DeviceCache()
