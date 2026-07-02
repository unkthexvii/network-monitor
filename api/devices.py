from __future__ import annotations
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, Query
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel, field_validator
from typing import List, Optional
import asyncio
import logging
import ipaddress
import json

from database.session import get_db
from database.models import Device, DeviceStatus, MinuteStat
from database.repository import get_all_devices, get_device, create_device, update_device, delete_device
from sqlalchemy import select, func
from datetime import datetime, timedelta, timezone
from fastapi.responses import StreamingResponse
import io
import csv
from core.pagination import paginate

logger = logging.getLogger(__name__)

router = APIRouter()


def _device_to_dict(device: Device, status_rec: Optional[DeviceStatus]) -> dict:
    """Build the standard JSON dict for a device + its live status.
    Used by both read_devices() and read_devices_paginated()."""
    return {
        "id": device.id,
        "name": device.name,
        "ip_address": device.ip_address,
        "device_type": device.device_type,
        "site": device.site,
        "location": device.location,
        "rack": device.rack,
        "vendor": device.vendor,
        "model": device.model,
        "check_interval": device.check_interval,
        "enabled": device.enabled,
        "remark": device.remark,
        "snmp_version": device.snmp_version,
        "snmp_community": "***" if device.snmp_community else None,
        "sys_name": status_rec.sys_name if status_rec else None,
        "sys_contact": status_rec.sys_contact if status_rec else None,
        "sys_location": status_rec.sys_location if status_rec else None,
        "sys_descr": status_rec.sys_descr if status_rec else None,
        "sys_uptime": status_rec.sys_uptime if status_rec else None,
        "client_count": status_rec.client_count if status_rec else None,
        "ap_count": status_rec.ap_count if status_rec else None,
        "serial_number": status_rec.serial_number if status_rec else None,
        "snmp_custom_data": json.loads(status_rec.snmp_custom_data) if (status_rec and status_rec.snmp_custom_data) else None,
        "status": status_rec.status if status_rec else "UNKNOWN",
        "latency_ms": status_rec.latency_ms if status_rec else 0,
        "packet_loss": status_rec.packet_loss if status_rec else 0,
        "last_seen": status_rec.last_seen.replace(tzinfo=timezone.utc).isoformat() if status_rec and status_rec.last_seen else None,
        "offline_since": status_rec.offline_since.replace(tzinfo=timezone.utc).isoformat() if status_rec and status_rec.offline_since else None
    }


class DeviceCreate(BaseModel):
    name: str = ""
    ip_address: str
    device_type: str = "Unknown"
    site: Optional[str] = None
    location: Optional[str] = None
    rack: Optional[str] = None
    vendor: Optional[str] = None
    model: Optional[str] = None
    check_interval: Optional[int] = None
    remark: Optional[str] = None
    snmp_version: Optional[str] = "None"
    snmp_community: Optional[str] = None
    snmp_v3_user: Optional[str] = None
    snmp_v3_auth: Optional[str] = None
    snmp_v3_priv: Optional[str] = None

    @field_validator("ip_address")
    @classmethod
    def validate_ip(cls, v: str) -> str:
        try:
            ipaddress.ip_address(v)
        except ValueError:
            raise ValueError(f"Invalid IP address: {v}")
        return v

    @field_validator("check_interval")
    @classmethod
    def validate_interval(cls, v: Optional[int]) -> Optional[int]:
        if v is not None and v < 10:
            raise ValueError("check_interval must be at least 10 seconds")
        return v

class DeviceUpdate(BaseModel):
    name: Optional[str] = None
    ip_address: Optional[str] = None
    device_type: Optional[str] = None
    site: Optional[str] = None
    location: Optional[str] = None
    rack: Optional[str] = None
    vendor: Optional[str] = None
    model: Optional[str] = None
    check_interval: Optional[int] = None
    enabled: Optional[int] = None
    remark: Optional[str] = None
    snmp_version: Optional[str] = None
    snmp_community: Optional[str] = None
    snmp_v3_user: Optional[str] = None
    snmp_v3_auth: Optional[str] = None
    snmp_v3_priv: Optional[str] = None

    @field_validator("ip_address")
    @classmethod
    def validate_ip(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        try:
            ipaddress.ip_address(v)
        except ValueError:
            raise ValueError(f"Invalid IP address: {v}")
        return v

    @field_validator("check_interval")
    @classmethod
    def validate_interval(cls, v: Optional[int]) -> Optional[int]:
        if v is not None and v < 10:
            raise ValueError("check_interval must be at least 10 seconds")
        return v

@router.get("/api/devices")
async def read_devices(session: AsyncSession = Depends(get_db)):
    """
    Returns all devices combined with their latest status.
    Uses a single JOIN query instead of N+1 queries.
    """
    # Single JOIN query — O(1) instead of O(N) database round-trips
    stmt = select(Device, DeviceStatus).outerjoin(
        DeviceStatus, Device.id == DeviceStatus.device_id
    )
    result = await session.execute(stmt)
    
    devices_list = []
    for device, status_rec in result:
        devices_list.append(_device_to_dict(device, status_rec))
    return devices_list


@router.get("/api/devices/names")
async def read_device_names(session: AsyncSession = Depends(get_db)):
    """Lightweight endpoint returning only id, name, ip_address, device_type.
    ~90% smaller payload than /api/devices — used by search comboboxes."""
    from sqlalchemy import select
    result = await session.execute(
        select(Device.id, Device.name, Device.ip_address, Device.device_type)
    )
    return [
        {"id": r[0], "name": r[1], "ip_address": r[2], "device_type": r[3]}
        for r in result
    ]

@router.get("/api/devices/export/csv")
async def export_devices_csv(session: AsyncSession = Depends(get_db)):
    stmt = select(Device).order_by(Device.id)
    result = await session.execute(stmt)
    devices = result.scalars().all()
    
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["id", "name", "type", "ipaddr", "check interval", "enabled"])
    
    for device in devices:
        writer.writerow([
            device.id,
            device.name,
            device.device_type,
            device.ip_address,
            device.check_interval,
            1 if device.enabled else 0
        ])
    
    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=devices_export.csv"}
    )

@router.get("/api/devices/paginated")
async def read_devices_paginated(
    page: int = Query(1, ge=1, description="Page number (1-based)"),
    limit: int = Query(10, ge=1, le=100, description="Items per page (max 100)"),
    status: Optional[str] = None,
    search: Optional[str] = None,
    subnet: Optional[str] = None,
    session: AsyncSession = Depends(get_db)
):
    """
    Returns devices using server-side pagination, with optional status/search filters.
    """
    stmt = select(Device, DeviceStatus).outerjoin(
        DeviceStatus, Device.id == DeviceStatus.device_id
    )
    
    if status and status != 'all':
        if status.upper() == "PAUSED":
            stmt = stmt.where(Device.enabled == 0)
        else:
            stmt = stmt.where(Device.enabled != 0)
            if status.upper() == "UNKNOWN":
                stmt = stmt.where((DeviceStatus.status == None) | (DeviceStatus.status == "UNKNOWN"))
            else:
                stmt = stmt.where(DeviceStatus.status == status.upper())
                
    if search:
        stmt = stmt.where(Device.name.like(f"%{search}%") | Device.ip_address.like(f"%{search}%"))
        
    if subnet and subnet != 'all':
        try:
            network = ipaddress.ip_network(subnet, strict=False)
            prefixlen = network.prefixlen
            if prefixlen >= 24:
                # /24 or smaller: match on first 3 octets
                prefix = str(network.network_address).rsplit('.', 1)[0]
                stmt = stmt.where(Device.ip_address.like(f"{prefix}.%"))
            elif prefixlen >= 16:
                # /16 to /23: match on first 2 octets
                prefix = str(network.network_address).rsplit('.', 2)[0]
                stmt = stmt.where(Device.ip_address.like(f"{prefix}.%"))
            elif prefixlen >= 8:
                # /8 to /15: match on first octet
                prefix = str(network.network_address).rsplit('.', 3)[0]
                stmt = stmt.where(Device.ip_address.like(f"{prefix}.%"))
            else:
                # /0 to /7: no effective filtering
                pass
        except ValueError:
            logger.warning(f"Invalid subnet filter value: {subnet}")
        
    if status and status.upper() == "OFFLINE":
        stmt = stmt.order_by(DeviceStatus.offline_since.desc().nullslast())
    else:
        stmt = stmt.order_by(Device.id)

    def transformer(result):
        devices_list = []
        for device, status_rec in result:
            devices_list.append(_device_to_dict(device, status_rec))
        return devices_list

    return await paginate(session, stmt, page, limit, transformer)
@router.post("/api/devices")
async def add_device(device: DeviceCreate, session: AsyncSession = Depends(get_db)):
    device_data = device.model_dump()
    if not device_data.get("name") or not device_data["name"].strip():
        device_data["name"] = device_data["ip_address"]
    if device_data.get("check_interval") is None:
        from core.utils import get_default_check_interval
        device_data["check_interval"] = get_default_check_interval(device_data.get("device_type", ""))
    new_device = await create_device(session, device_data)
    
    from core.device_cache import device_cache
    await device_cache.refresh_from_db()
    
    return {"id": new_device.id, "message": "Device created successfully"}

@router.post("/api/devices/bulk")
async def add_devices_bulk(devices: List[DeviceCreate], session: AsyncSession = Depends(get_db)):
    added_ids = []
    skipped = 0
    errors = 0
    
    # Pre-fetch existing IPs to skip duplicates
    from sqlalchemy import select
    result = await session.execute(select(Device.ip_address))
    existing_ips = {row[0] for row in result.fetchall()}
    from core.utils import get_default_check_interval
    
    for device in devices:
        try:
            device_data = device.model_dump()
            ip = device_data.get("ip_address")
            if ip in existing_ips:
                skipped += 1
                continue
            if not device_data.get("name") or not device_data["name"].strip():
                device_data["name"] = ip
            if device_data.get("check_interval") is None:
                device_data["check_interval"] = get_default_check_interval(device_data.get("device_type", ""))
            dev = Device(**device_data)
            session.add(dev)
            await session.flush()
            existing_ips.add(ip)
            status = DeviceStatus(device_id=dev.id, status="UNKNOWN", fail_count=0)
            session.add(status)
            added_ids.append(dev.id)
        except Exception:
            errors += 1
            continue
    
    await session.commit()
    
    from core.device_cache import device_cache
    await device_cache.refresh_from_db()
    
    return {"added_count": len(added_ids), "skipped": skipped, "errors": errors, "message": f"{len(added_ids)} device(s) created"}

@router.put("/api/devices/{device_id}")
async def edit_device(device_id: int, device: DeviceUpdate, session: AsyncSession = Depends(get_db)):
    old_device = await get_device(session, device_id)
    if not old_device:
        raise HTTPException(status_code=404, detail="Device not found")
        
    old_enabled = old_device.enabled

    updated = await update_device(session, device_id, device.model_dump(exclude_unset=True))
    if not updated:
        raise HTTPException(status_code=404, detail="Device not found")
        
    if old_enabled != updated.enabled:
        from database.models import Alert
        from core.alert_engine import _notify_callback
        
        new_status = "PAUSED" if updated.enabled == 0 else "RESUMED"
        msg = f"Monitoring for device {updated.name} ({updated.ip_address}) was {new_status.lower()}."
        
        alert = Alert(device_id=device_id, alert_type=new_status, message=msg)
        session.add(alert)
        await session.commit()
        
        if _notify_callback:
            event_data = {
                "device_id": device_id,
                "device_name": updated.name,
                "status": new_status,
                "message": msg,
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
            try:
                await _notify_callback("status_change", event_data)
            except Exception as e:
                logger.warning(f"SSE notify failed for device {device_id}: {e}")

    from core.device_cache import device_cache
    await device_cache.refresh_from_db()
    
    return {"message": "Device updated successfully"}

@router.delete("/api/devices/{device_id}")
async def remove_device(device_id: int, session: AsyncSession = Depends(get_db)):
    await delete_device(session, device_id)
    
    from core.device_cache import device_cache
    await device_cache.refresh_from_db()
    
    return {"message": "Device deleted successfully"}

@router.get("/api/devices/{device_id}/stats")
async def get_device_stats(device_id: int, timeframe: str = "24h", session: AsyncSession = Depends(get_db)):
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    if timeframe == "24h":
        start_time = now - timedelta(hours=24)
    elif timeframe == "7d":
        start_time = now - timedelta(days=7)
    elif timeframe == "30d":
        start_time = now - timedelta(days=30)
    else:
        start_time = datetime.min
        
    stmt = select(
        func.avg(MinuteStat.avg_latency).label("avg_latency"),
        func.avg(MinuteStat.packet_loss).label("avg_loss"),
        func.avg(MinuteStat.uptime_percent).label("avg_uptime")
    ).where(MinuteStat.device_id == device_id, MinuteStat.minute >= start_time)
    
    result = await session.execute(stmt)
    row = result.first()
    
    if not row or row.avg_latency is None:
        return {"latency_ms": 0, "packet_loss": 0, "uptime_percent": 0}
        
    return {
        "latency_ms": round(row.avg_latency, 1) if row.avg_latency is not None else 0,
        "packet_loss": round(row.avg_loss, 1) if row.avg_loss is not None else 0,
        "uptime_percent": round(row.avg_uptime, 1) if row.avg_uptime is not None else 100.0
    }

@router.get("/api/devices/subnets")
async def get_detected_subnets():
    """Auto-detect local IPv4 subnets using fast python socket (assuming /24 default)."""
    import socket
    subnets = set()
    try:
        hostname = socket.gethostname()
        ips = socket.gethostbyname_ex(hostname)[2]
        for ip in ips:
            if ip.startswith("169.254.") or ip.startswith("127."):
                continue
            
            # Assume a /24 subnet for rapid detection
            parts = ip.split('.')
            if len(parts) == 4:
                network = f"{parts[0]}.{parts[1]}.{parts[2]}.0/24"
                subnets.add(network)
    except Exception as e:
        logger.warning(f"Error detecting subnets: {e}")
        # Fallback
        subnets.add("192.168.1.0/24")
        subnets.add("10.0.0.0/24")
    
    return {"subnets": list(subnets)}

class DiscoverRequest(BaseModel):
    subnet: str

    @field_validator("subnet")
    @classmethod
    def validate_subnet(cls, v: str) -> str:
        try:
            network = ipaddress.ip_network(v, strict=False)
            if network.prefixlen < 16:
                raise ValueError(f"Subnet {v} is too large (minimum /16)")
        except ValueError as e:
            if "too large" in str(e):
                raise
            raise ValueError(f"Invalid CIDR subnet: {v}")
        return v

async def lan_sweep_worker(subnet: str):
    """
    Background worker to sweep a LAN subnet.
    """
    from core.icmp_engine import ping_devices
    import ipaddress
    
    try:
        network = ipaddress.ip_network(subnet, strict=False)
        # Limit to /24 or smaller to prevent massive sweeps
        if network.prefixlen < 24:
            logger.warning(f"Subnet {subnet} is larger than /24, skipping discovery")
            # Notify frontend that discovery was skipped
            from api.stream import sse_publisher
            await sse_publisher("discover_error", {"subnet": subnet, "reason": f"Subnet {subnet} is larger than /24, skipping discovery"})
            return
            
        ips = [str(ip) for ip in network.hosts()]
        results = await ping_devices(ips, count=3, timeout=1.5)
        
        active_ips = [r["ip_address"] for r in results if r["status"] == "ONLINE"]
        
        # Notify the frontend via SSE that discovery is complete
        from api.stream import sse_publisher
        await sse_publisher("discover_complete", {"subnet": subnet, "active_ips": active_ips})
        
    except Exception as e:
        logger.error(f"Discovery error for {subnet}: {e}", exc_info=True)
        # Notify frontend of the error so it doesn't hang indefinitely
        try:
            from api.stream import sse_publisher
            await sse_publisher("discover_error", {"subnet": subnet, "reason": str(e)})
        except Exception:
            pass

@router.post("/api/devices/discover")
async def discover_lan(req: DiscoverRequest, background_tasks: BackgroundTasks):
    background_tasks.add_task(lan_sweep_worker, req.subnet)
    return {"message": "Discovery started"}


@router.get("/api/test/snmp/{device_id}")
async def test_snmp_device(device_id: int, session: AsyncSession = Depends(get_db)):
    """Manually trigger an SNMP poll for a single device and return the raw result."""
    from core.snmp_engine import fetch_snmp_data
    from database.session import async_session

    # Fetch device from main DB
    stmt = select(Device).where(Device.id == device_id)
    result = await session.execute(stmt)
    device = result.scalar_one_or_none()
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")
    if device.snmp_version not in ("v2c", "v3"):
        return {
            "device_id": device_id,
            "ip": device.ip_address,
            "name": device.name,
            "status": "SKIPPED",
            "reason": f"SNMP version is '{device.snmp_version}' (not v2c/v3)"
        }

    import time as _time
    t0 = _time.monotonic()
    try:
        data = await fetch_snmp_data(
            device_ip=device.ip_address,
            snmp_version=device.snmp_version,
            community=device.snmp_community,
            v3_user=device.snmp_v3_user,
            v3_auth=device.snmp_v3_auth,
            v3_priv=device.snmp_v3_priv,
            device_type=device.device_type,
            device_name=device.name
        )
    except Exception as e:
        return {
            "device_id": device_id,
            "ip": device.ip_address,
            "name": device.name,
            "status": "EXCEPTION",
            "error": str(e),
            "elapsed_s": round(_time.monotonic() - t0, 2)
        }
    elapsed = round(_time.monotonic() - t0, 2)

    if data is None:
        return {
            "device_id": device_id,
            "ip": device.ip_address,
            "name": device.name,
            "community_masked": "***" if device.snmp_community else "??",
            "status": "TIMEOUT",
            "elapsed_s": elapsed
        }
    return {
        "device_id": device_id,
        "ip": device.ip_address,
        "name": device.name,
        "status": "OK",
        "sys_name": data.get("sys_name"),
        "sys_descr": data.get("sys_descr"),
        "sys_uptime": data.get("sys_uptime"),
        "serial_number": data.get("serial_number"),
        "elapsed_s": elapsed
    }
