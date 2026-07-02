from __future__ import annotations
from typing import List, Optional
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession
from .models import (
    Device, DeviceStatus, Alert, TopologyLink, TopologyNode,
    MinuteStat
)
from core.ping_buffer import ping_buffer
import logging

logger = logging.getLogger(__name__)


async def get_all_devices(session: AsyncSession) -> List[Device]:
    result = await session.execute(select(Device))
    return result.scalars().all()


async def get_device(session: AsyncSession, device_id: int) -> Optional[Device]:
    result = await session.execute(select(Device).where(Device.id == device_id))
    return result.scalar_one_or_none()


async def create_device(session: AsyncSession, device_data: dict) -> Device:
    new_device = Device(**device_data)
    session.add(new_device)
    await session.commit()
    await session.refresh(new_device)

    # Initialize DeviceStatus
    status = DeviceStatus(device_id=new_device.id, status="UNKNOWN", fail_count=0)
    session.add(status)
    await session.commit()

    return new_device


async def update_device(session: AsyncSession, device_id: int, device_data: dict) -> Optional[Device]:
    device = await get_device(session, device_id)
    if not device:
        return None
    for key, value in device_data.items():
        if value == "***" and key in ("snmp_community", "snmp_v3_auth", "snmp_v3_priv"):
            continue  # Preserve existing value when masked placeholder is sent back
        if hasattr(device, key):
            setattr(device, key, value)
    await session.commit()
    await session.refresh(device)
    return device


async def delete_device(session: AsyncSession, device_id: int):
    """
    Delete a device and all related records.
    With ForeignKey CASCADE this is handled by the DB, but we explicitly
    clean up for SQLite compatibility (PRAGMA foreign_keys may be off).
    """
    # Clear in-memory ping buffer for this device
    await ping_buffer.clear_device(device_id)
    await session.execute(delete(MinuteStat).where(MinuteStat.device_id == device_id))
    await session.execute(delete(Alert).where(Alert.device_id == device_id))
    await session.execute(delete(DeviceStatus).where(DeviceStatus.device_id == device_id))
    await session.execute(delete(TopologyNode).where(TopologyNode.device_id == device_id))
    await session.execute(delete(TopologyLink).where(
        (TopologyLink.parent_device_id == device_id) |
        (TopologyLink.child_device_id == device_id)
    ))
    await session.execute(delete(Device).where(Device.id == device_id))
    await session.commit()
