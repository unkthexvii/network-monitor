from __future__ import annotations
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc
from typing import Optional
from datetime import datetime, timezone, timedelta

from database.session import get_db
from database.models import Alert, Device
import logging

logger = logging.getLogger(__name__)

router = APIRouter()

from core.utils import build_alert_messages, parse_utc_iso

from sqlalchemy import func
from sqlalchemy.orm import aliased

from core.pagination import paginate

@router.get("/api/alerts")
async def get_alerts(
    device_id: Optional[int] = None,
    status: Optional[str] = None,
    time_filter: Optional[str] = None, # '24h', '7d', '30d', 'custom', 'all'
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    page: int = 1,
    limit: int = 10,
    session: AsyncSession = Depends(get_db)
):
    """
    Returns paginated alerts.
    If device_id is provided, it paginates the individual alerts for that device.
    If device_id is omitted, it paginates the Devices and returns the top 50 alerts per device.
    """
    
    conditions = []
    if status and status.upper() != "ALL":
        conditions.append(Alert.alert_type == status.upper())
        
    if time_filter and time_filter != "all":
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        if time_filter == "24h":
            delta = timedelta(hours=24)
        elif time_filter == "7d":
            delta = timedelta(days=7)
        elif time_filter == "30d":
            delta = timedelta(days=30)
        else:
            delta = None
            
        if delta:
            conditions.append(Alert.created_at >= (now - delta))
            
    if time_filter == "custom" and start_date and end_date:
        try:
            s_time = parse_utc_iso(start_date)
            e_time = parse_utc_iso(end_date)
            conditions.append(Alert.created_at >= s_time)
            conditions.append(Alert.created_at <= e_time)
        except Exception as e:
            logger.warning(f"Failed to parse custom date range: start={start_date}, end={end_date}: {e}")

    def transformer(result):
        rows = list(result)
        alerts_objs = [row[0] for row in rows]
        alert_messages = build_alert_messages(alerts_objs)
        
        alerts_data = []
        for i, row in enumerate(rows):
            alert = row[0]
            dev_name = row[1]
            dev_ip = row[2]
            dev_remark = row[3] if len(row) > 3 else None
            alerts_data.append({
                "id": alert.id,
                "device_id": alert.device_id,
                "device_name": dev_name,
                "ip_address": dev_ip,
                "remark": dev_remark,
                "alert_type": alert.alert_type,
                "message": alert_messages[i],
                "timestamp": alert.created_at.replace(tzinfo=timezone.utc).isoformat() if alert.created_at else None
            })
        return alerts_data

    if device_id:
        # Paginating Alerts directly for a specific device
        stmt = select(Alert, Device.name, Device.ip_address, Device.remark).join(Device, Alert.device_id == Device.id)
        stmt = stmt.where(Alert.device_id == device_id)
        for cond in conditions:
            stmt = stmt.where(cond)
        stmt = stmt.order_by(desc(Alert.created_at))
        
        return await paginate(session, stmt, page, limit, transformer)
        
    else:
        # Paginating Devices to group alerts
        dev_stmt = select(Device).join(Alert, Device.id == Alert.device_id)
        for cond in conditions:
            dev_stmt = dev_stmt.where(cond)
        dev_stmt = dev_stmt.group_by(Device.id).order_by(desc(func.max(Alert.created_at)))
        
        count_stmt = select(func.count()).select_from(dev_stmt.subquery())
        total_devices = (await session.execute(count_stmt)).scalar() or 0
        
        if total_devices == 0:
            return {"items": [], "total": 0, "page": page, "pages": 0}
            
        offset = (page - 1) * limit
        paginated_devs = (await session.execute(dev_stmt.offset(offset).limit(limit))).scalars().all()
        dev_ids = [d.id for d in paginated_devs]
        
        # Now fetch top 50 alerts for these specific devices
        base_alert = select(Alert).where(Alert.device_id.in_(dev_ids))
        for cond in conditions:
            base_alert = base_alert.where(cond)
            
        subq = base_alert.add_columns(
            func.row_number().over(
                partition_by=Alert.device_id,
                order_by=Alert.created_at.desc()
            ).label('rn')
        ).subquery()
        
        AlertAlias = aliased(Alert, subq)
        
        stmt = select(AlertAlias, Device.name, Device.ip_address, Device.remark).join(
            Device, AlertAlias.device_id == Device.id
        ).where(subq.c.rn <= 10).order_by(desc(AlertAlias.created_at))
        
        result = await session.execute(stmt)
        alerts_data = transformer(result)
        
        return {
            "items": alerts_data,
            "total": total_devices,
            "page": page,
            "pages": (total_devices + limit - 1) // limit
        }
