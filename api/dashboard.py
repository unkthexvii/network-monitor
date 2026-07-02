from __future__ import annotations
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, desc, and_
from datetime import timezone, datetime, timedelta

from database.session import get_db
from database.models import Device, DeviceStatus, Alert
from core.pagination import paginate
import logging

logger = logging.getLogger(__name__)

router = APIRouter()

@router.get("/api/dashboard/stats")
async def get_dashboard_stats(session: AsyncSession = Depends(get_db)):
    """
    Returns total, online, offline, and paused device counts.
    Uses a single query with CASE/WHEN instead of 4 separate COUNT queries.
    """
    from sqlalchemy import case
    
    stmt = select(
        func.count(Device.id).label('total'),
        func.sum(case(
            (and_(DeviceStatus.status == 'ONLINE', Device.enabled != 0), 1),
            else_=0
        )).label('online'),
        func.sum(case(
            (and_(DeviceStatus.status == 'OFFLINE', Device.enabled != 0), 1),
            else_=0
        )).label('offline'),
        func.sum(case(
            (Device.enabled == 0, 1),
            else_=0
        )).label('paused')
    ).outerjoin(DeviceStatus, Device.id == DeviceStatus.device_id)
    
    row = (await session.execute(stmt)).one()
    total = row.total or 0
    online = row.online or 0
    offline = row.offline or 0
    paused = row.paused or 0
    
    return {
        "total": total,
        "online": online,
        "offline": offline,
        "paused": paused,
        "unknown": total - (online + offline + paused)
    }

@router.get("/api/dashboard/events")
async def get_recent_events(
    page: int = 1,
    limit: int = 10,
    session: AsyncSession = Depends(get_db)
):
    """
    Returns all recent alerts for the event feed within the last 24 hours, using server-side pagination.
    """
    last_24h = datetime.now(timezone.utc) - timedelta(hours=24)
    stmt = select(Alert, Device.name, Device.ip_address, Device.remark).join(Device, Alert.device_id == Device.id).where(
        Alert.created_at >= last_24h, 
        Alert.alert_type != "INITIALIZED"
    ).order_by(desc(Alert.created_at))
    
    def transformer(result):
        events = []
        for alert, dev_name, dev_ip, dev_remark in result:
            events.append({
                "id": alert.id,
                "device_name": dev_name,
                "ip_address": dev_ip,
                "remark": dev_remark,
                "alert_type": alert.alert_type,
                "message": alert.message,
                "timestamp": alert.created_at.replace(tzinfo=timezone.utc).isoformat() if alert.created_at else None
            })
        return events
        
    if limit == 0:
        # No pagination, fetch everything
        result = (await session.execute(stmt)).all()
        items = transformer(result)
        return {"items": items, "total": len(items), "page": 1, "pages": 1}
        
    return await paginate(session, stmt, page, limit, transformer)
