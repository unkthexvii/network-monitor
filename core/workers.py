from __future__ import annotations
import asyncio
import logging
from datetime import datetime, timezone
from database.session import async_session
from database.models import DeviceStatus, Alert
from core.icmp_engine import ping_devices
from core.ping_buffer import ping_buffer, PingSample
from core.device_cache import CachedDevice, device_cache
from core.config import OFFLINE_THRESHOLD, ONLINE_THRESHOLD
from core.alert_engine import build_alert_message, notify_state_change

logger = logging.getLogger(__name__)

# Global semaphore to limit concurrent chunk processing.
# 10 chunks * 50 devices = 500 max concurrent pings globally.
_ping_chunk_semaphore = None

def _get_ping_chunk_semaphore():
    global _ping_chunk_semaphore
    if _ping_chunk_semaphore is None:
        _ping_chunk_semaphore = asyncio.Semaphore(10)
    return _ping_chunk_semaphore

async def _process_ping_chunk(devices: list):
    sem = _get_ping_chunk_semaphore()
    async with sem:
        ips = [d.ip_address for d in devices]
    
        # Perform the actual ping using icmplib
        results = await ping_devices(ips)

    # Map results by IP for quick lookup
    result_map = {res["ip_address"]: res for res in results}

    # Single pass: build buffer batch AND process state transitions
    buffer_batch = {}
    db_updates = []
    state_transitions = []
    sse_events_to_dispatch = []

    for dev in devices:
        res = result_map.get(dev.ip_address)
        if not res:
            continue

        # Record ping sample for the in-memory buffer
        buffer_batch[dev.id] = PingSample(
            status=res["status"],
            latency_ms=res["latency_ms"],
            packet_loss=res["packet_loss"],
        )

        ping_status = res["status"]

        dev.latency_ms = res["latency_ms"]
        dev.packet_loss = res["packet_loss"]

        old_status = dev.status
        new_status = old_status

        # Apply thresholds
        if ping_status == "ONLINE":
            dev.last_seen = datetime.now(timezone.utc).replace(tzinfo=None)
            dev.fail_count = 0
            dev.first_fail_time = None  # reset on success
            dev.recovery_count = (dev.recovery_count or 0) + 1
            if dev.recovery_count >= ONLINE_THRESHOLD:
                dev.recovery_count = 0
                dev.offline_since = None
                new_status = "ONLINE"
        else:
            dev.recovery_count = 0
            dev.fail_count = (dev.fail_count or 0) + 1
            # Track the first failure time of this streak for accurate offline_since
            if dev.fail_count == 1:
                dev.first_fail_time = datetime.now(timezone.utc).replace(tzinfo=None)
            if dev.fail_count >= OFFLINE_THRESHOLD:
                new_status = "OFFLINE"
                if old_status != "OFFLINE":
                    # Use actual elapsed time since first failure instead of estimated back-off
                    first_fail = dev.first_fail_time or datetime.now(timezone.utc).replace(tzinfo=None)
                    dev.offline_since = first_fail
            else:
                new_status = old_status if old_status != "UNKNOWN" else "UNKNOWN"

        # Always update DB metrics
        dev.status = new_status
        db_updates.append((dev, new_status, old_status))

        # State Transition — generate SSE and Alerts
        if old_status != new_status:
            state_transitions.append((dev, new_status, old_status))

            # Store events to notify AFTER commit
            sse_events_to_dispatch.append({
                "dev": dev,
                "old_status": old_status,
                "new_status": new_status
            })

    # Flush all ping samples to the in-memory buffer in one shot
    await ping_buffer.append_batch(buffer_batch)

    # Only open a DB session if there are actual updates to persist
    if db_updates:
        async with async_session() as session:
            from sqlalchemy import select
            
            # OPTIMIZATION: Use chunked in_ queries to avoid SQLite's 999-variable limit
            dev_ids = [d.id for d, _, _ in db_updates]
            existing_map = {}
            CHUNK_SIZE = 500
            for i in range(0, len(dev_ids), CHUNK_SIZE):
                chunk = dev_ids[i:i + CHUNK_SIZE]
                stmt = select(DeviceStatus).where(DeviceStatus.device_id.in_(chunk))
                records = (await session.execute(stmt)).scalars().all()
                for r in records:
                    existing_map[r.device_id] = r
            
            for dev, new_status, old_status in db_updates:
                status_record = existing_map.get(dev.id)

                if not status_record:
                    status_record = DeviceStatus(device_id=dev.id)
                    session.add(status_record)

                status_record.status = dev.status
                status_record.latency_ms = dev.latency_ms
                status_record.packet_loss = dev.packet_loss
                status_record.last_seen = dev.last_seen
                status_record.offline_since = dev.offline_since
                status_record.fail_count = dev.fail_count
                status_record.recovery_count = dev.recovery_count

            for dev, new_status, old_status in state_transitions:
                # Generate Alert using the shared helper from alert_engine
                message, alert_type = build_alert_message(old_status, new_status, dev)

                logger.warning(message) if new_status == "OFFLINE" else logger.info(message)

                alert = Alert(
                    device_id=dev.id,
                    alert_type=alert_type,
                    message=message
                )
                session.add(alert)

            await session.commit()
            logger.info(f"State changes persisted for {len(db_updates)} devices")

        # Notify clients (SSE) AFTER DB commit
        for evt in sse_events_to_dispatch:
            dev = evt["dev"]
            old_status = evt["old_status"]
            new_status = evt["new_status"]
            await notify_state_change(old_status, new_status, dev)

async def ping_worker(devices: list):
    """
    Takes a batch of CachedDevice objects, pings them concurrently in chunks,
    buffers the results in memory, and processes state transitions incrementally.
    """
    if not devices:
        return

    try:
        chunk_size = 50
        for i in range(0, len(devices), chunk_size):
            chunk = devices[i:i+chunk_size]
            await _process_ping_chunk(chunk)

    except Exception as e:
        logger.error(f"ping_worker failed for {len(devices)} devices: {e}", exc_info=True)
    finally:
        # Always release the per-device concurrency lock
        for dev in devices:
            dev.last_ping_start = 0.0
