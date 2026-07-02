from __future__ import annotations
import os
import asyncio
import logging
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from datetime import datetime, timedelta, timezone
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, desc
from typing import Optional

from database.session import get_db
from database.models import Device, MinuteStat, Alert
from reporting.pdf_generator import generate_pdf_report
from core.utils import format_alert_message, get_timeframe_bounds
from core.cache import report_cache

logger = logging.getLogger(__name__)

router = APIRouter()

@router.get("/api/reports/generate")
async def generate_report(
    device_id: int,
    time_filter: str = "7d", # e.g., 24h, 7d, 30d, custom
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    session: AsyncSession = Depends(get_db)
):
    """
    Generates a PDF report for a specific device and returns the file.
    PDF generation runs in a thread pool to avoid blocking the event loop.
    """
    stmt = select(Device).where(Device.id == device_id)
    device = (await session.execute(stmt)).scalar_one_or_none()
    
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")
        
    # generate_pdf_report is async (it queries DB internally), so await it directly.
    # fpdf2 rendering is fast enough for simple reports; for heavy reports,
    # consider offloading to BackgroundTasks and returning a job ID.
    try:
        file_path = await generate_pdf_report(session, device, time_filter, start_date, end_date)
    except Exception as e:
        logger.error(f"PDF generation failed for device {device_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to generate PDF")
    
    if not os.path.exists(file_path):
        raise HTTPException(status_code=500, detail="Failed to generate PDF")
        
    from starlette.background import BackgroundTask
    return FileResponse(
        path=file_path, 
        filename=os.path.basename(file_path), 
        media_type='application/pdf',
        background=BackgroundTask(os.remove, file_path)
    )

@router.get("/api/reports/ui_data")
async def get_report_ui_data(
    device_id: int,
    timeframe: str = "24h",
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    session: AsyncSession = Depends(get_db)
):
    # Check cache first — this data only changes once per minute
    cache_key = f"ui_data:{device_id}:{timeframe}:{start_date}:{end_date}"
    cached = report_cache.get(cache_key)
    if cached is not None:
        return cached

    start_time, end_time, blocks_count, block_delta, _ = get_timeframe_bounds(timeframe, start_date, end_date)

    from core.archive_query import get_federated_report_data

    # Single batch query — one connection, one ATTACH cycle for all 3 data types
    stats_dict, incident_count, alerts, all_stats = await get_federated_report_data(
        device_id, start_time, end_time, alert_limit=0  # no alerts needed for UI data
    )
    global_uptime = round(stats_dict["avg_uptime"], 1)
    avg_latency = round(stats_dict["avg_latency"], 1)

    # Build blocks
    blocks = []
    current_block_start = start_time
    stat_idx = 0
    num_stats = len(all_stats)
    
    for i in range(blocks_count):
        block_end = current_block_start + block_delta
        
        block_uptimes = []
        has_incident = False
        while stat_idx < num_stats and all_stats[stat_idx].minute < block_end:
            if all_stats[stat_idx].minute >= current_block_start:
                if all_stats[stat_idx].uptime_percent is not None:
                    block_uptimes.append(all_stats[stat_idx].uptime_percent)
                    if all_stats[stat_idx].uptime_percent < 100.0:
                        has_incident = True
            stat_idx += 1
            
        if block_uptimes:
            block_avg = sum(block_uptimes) / len(block_uptimes)
        else:
            block_avg = 100.0 # assume 100 if no data
            
        blocks.append({
            "start": current_block_start.replace(tzinfo=timezone.utc).isoformat(),
            "end": block_end.replace(tzinfo=timezone.utc).isoformat(),
            "uptime": round(block_avg, 1),
            "has_incident": has_incident
        })
        current_block_start = block_end

    result = {
        "global_uptime": global_uptime,
        "avg_latency": avg_latency,
        "incident_count": incident_count,
        "sla_compliance": global_uptime,
        "heatmap_blocks": blocks
    }
    report_cache.set(cache_key, result)
    return result
