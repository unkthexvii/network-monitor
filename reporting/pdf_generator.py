from __future__ import annotations
import os
import asyncio
import logging
from datetime import datetime, timezone, timedelta
from fpdf import FPDF
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc, func
from typing import Optional

try:
    import matplotlib
    matplotlib.use('Agg')  # Non-interactive backend, safe for threads
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates
    MATPLOTLIB_AVAILABLE = True
except ImportError as e:
    # On Windows 7, matplotlib/kiwisolver often fail to load due to missing C++ redistributable or UCRT.
    import logging
    logging.getLogger(__name__).warning(f"Matplotlib not available, charts will be disabled: {e}")
    MATPLOTLIB_AVAILABLE = False

import json
from database.models import Device, Alert, MinuteStat, DeviceStatus
from core.utils import format_alert_message, build_alert_messages, get_timeframe_bounds

logger = logging.getLogger(__name__)

def format_local_time(dt: datetime, include_seconds: bool = False) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    local_dt = dt.astimezone()
    if include_seconds:
        return local_dt.strftime("%d/%m/%Y %I:%M:%S %p")
    return local_dt.strftime("%d/%m/%Y %I:%M %p")


async def generate_pdf_report(session: AsyncSession, device: Device, time_filter: str, start_date: Optional[str] = None, end_date: Optional[str] = None) -> str:
    """
    Generates a professional PDF report for a device.
    Phase 1: Async DB queries to fetch all data.
    Phase 2: Offloads CPU-bound PDF/chart rendering to a thread pool
             so the FastAPI event loop is never blocked.
    """
    reports_dir = os.path.join(os.getcwd(), "reports")
    os.makedirs(reports_dir, exist_ok=True)

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    safe_name = "".join(c for c in device.name if c.isalnum() or c in " _-").strip().replace(" ", "_") or f"device_{device.id}"
    filename = f"report_{safe_name}_{timestamp}.pdf"
    file_path = os.path.join(reports_dir, filename)

    start_time, end_time, blocks_count, block_delta, block_label = get_timeframe_bounds(time_filter, start_date, end_date)

    # === Phase 1: Async DB queries ===
    from core.archive_query import get_federated_report_data

    # 1-4. Fetch all report data over a single shared DB connection
    stats_dict, incident_count, alerts, all_stats = await get_federated_report_data(
        device.id, start_time, end_time, alert_limit=50
    )
    global_uptime = round(stats_dict["avg_uptime"], 1)
    avg_latency = round(stats_dict["avg_latency"], 1)
    avg_packet_loss = round(stats_dict.get("avg_packet_loss", 0.0), 2)

    # 2.5 Fetch SNMP Data
    snmp_data_parsed = None
    if device.snmp_version not in (None, "None"):
        stmt_status = select(DeviceStatus).where(DeviceStatus.device_id == device.id).order_by(desc(DeviceStatus.last_seen)).limit(1)
        latest_status = (await session.execute(stmt_status)).scalar_one_or_none()
        if latest_status and latest_status.snmp_custom_data:
            try:
                snmp_data_parsed = json.loads(latest_status.snmp_custom_data)
            except json.JSONDecodeError:
                pass

        # Always merge column-based fields into SNMP display dict
        if latest_status:
            if snmp_data_parsed is None:
                snmp_data_parsed = {}
            if latest_status.sys_name:
                snmp_data_parsed["system_name"] = latest_status.sys_name
            if latest_status.sys_uptime:
                snmp_data_parsed["system_uptime"] = latest_status.sys_uptime
            if latest_status.sys_descr:
                snmp_data_parsed["description"] = latest_status.sys_descr
            if latest_status.serial_number:
                snmp_data_parsed["serial_number"] = latest_status.serial_number
            if latest_status.client_count is not None:
                snmp_data_parsed["client_count"] = latest_status.client_count
            if latest_status.ap_count is not None:
                snmp_data_parsed["ap_count"] = latest_status.ap_count
            if latest_status.sys_contact:
                snmp_data_parsed["system_contact"] = latest_status.sys_contact
            if latest_status.sys_location:
                snmp_data_parsed["system_location"] = latest_status.sys_location

    # 3. Build Blocks Data

    blocks = []
    current_block_start = start_time
    stat_idx = 0
    num_stats = len(all_stats)
    
    for i in range(blocks_count):
        block_end = current_block_start + block_delta
        
        block_uptimes = []
        block_latencies = []
        while stat_idx < num_stats and all_stats[stat_idx].minute < block_end:
            if all_stats[stat_idx].minute >= current_block_start:
                if all_stats[stat_idx].uptime_percent is not None:
                    block_uptimes.append(all_stats[stat_idx].uptime_percent)
                if all_stats[stat_idx].avg_latency is not None:
                    block_latencies.append(all_stats[stat_idx].avg_latency)
            stat_idx += 1
            
        block_avg_up = sum(block_uptimes) / len(block_uptimes) if block_uptimes else 100.0
        block_avg_lat = sum(block_latencies) / len(block_latencies) if block_latencies else 0.0
            
        blocks.append({
            "start": current_block_start,
            "end": block_end,
            "uptime": round(block_avg_up, 1),
            "latency": round(block_avg_lat, 1)
        })
        current_block_start = block_end

    # Pre-compute alert messages while we still have access to the ORM objects
    from core.utils import build_alert_messages
    alert_messages = build_alert_messages(alerts)
    alert_rows = []
    for i, alert in enumerate(alerts):
        alert_rows.append({
            "timestamp": alert.created_at,
            "alert_type": alert.alert_type,
            "message": alert_messages[i]
        })

    # Collect all data into a plain dict for the sync renderer
    report_data = {
        "device_name": device.name,
        "ip_address": device.ip_address,
        "snmp_enabled": device.snmp_version not in (None, "None"),
        "snmp_data": snmp_data_parsed,
        "time_filter": time_filter,
        "start_time": start_time,
        "end_time": end_time,
        "site": device.site,
        "location": device.location,
        "rack": device.rack,
        "vendor": device.vendor,
        "model": device.model,
        "device_type": device.device_type,
        "check_interval": device.check_interval,
        "global_uptime": global_uptime,
        "avg_latency": avg_latency,
        "avg_packet_loss": avg_packet_loss,
        "incident_count": incident_count,
        "blocks": blocks,
        "alerts": alert_rows,
        "file_path": file_path,
        "reports_dir": reports_dir,
        "device_id": device.id,
        "timestamp": timestamp,
    }

    # === Phase 2: Offload CPU-bound rendering to thread pool ===
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, _render_pdf, report_data)
    
    logger.info(f"Generated PDF report: {file_path}")
    return file_path


def draw_two_column_row(pdf, label: str, value: str, fill: bool, key_font=""):
    pdf.set_font("helvetica", "", 10)
    val_str = str(value or '--')
    words = val_str.split(' ')
    lines = []
    curr_line = ""
    for w in words:
        test_line = f"{curr_line} {w}".strip()
        if pdf.get_string_width(f" {test_line}") < 128:
            curr_line = test_line
        else:
            if curr_line:
                lines.append(curr_line)
                curr_line = w
            else:
                lines.append(w)
                curr_line = ""
    if curr_line:
        lines.append(curr_line)
    
    if not lines:
        lines = ["--"]

    row_h = max(1, len(lines)) * 7
    
    if pdf.get_y() + row_h > 280:
        pdf.add_page()
        
    x = pdf.get_x()
    y = pdf.get_y()
    
    style = 'FD' if fill else 'D'
    
    pdf.rect(x, y, 60, row_h, style=style)
    pdf.set_xy(x, y)
    pdf.set_font("helvetica", key_font, 10)
    pdf.cell(60, 7, f"  {label}:", border=0)
    
    pdf.rect(x + 60, y, 130, row_h, style=style)
    pdf.set_font("helvetica", "", 10)
    for i, line in enumerate(lines):
        pdf.set_xy(x + 60, y + (i * 7))
        pdf.cell(130, 7, f" {line}", border=0)
        
    pdf.set_y(y + row_h)


def _render_pdf(data: dict):
    """
    Synchronous, CPU-bound PDF rendering. Runs in a thread pool
    to avoid blocking the FastAPI async event loop.
    """
    pdf = FPDF()
    pdf.add_page()
    pdf.set_auto_page_break(auto=True, margin=15)

    file_path = data["file_path"]
    time_filter = data["time_filter"]
    blocks = data["blocks"]
    alerts = data["alerts"]

    # Header
    pdf.set_font("helvetica", "B", 20)
    pdf.cell(0, 10, "Performance Report", ln=True, align='C')
    pdf.set_font("helvetica", "B", 14)
    pdf.multi_cell(0, 6, f"Device: {data['device_name']} ({data['ip_address']})", align='C')
    
    # Fix X position after multi_cell leaves it at the right margin
    pdf.set_x(pdf.l_margin)
    
    pdf.set_font("helvetica", "", 10)
    pdf.set_text_color(100, 100, 100)
    
    if time_filter == 'custom':
        start_str = format_local_time(data["start_time"], False)
        end_str = format_local_time(data["end_time"], False)
        time_text = f"Timeframe: Custom ({start_str} to {end_str})"
    else:
        time_text = f"Timeframe: Last {time_filter}"
    
    gen_time = format_local_time(datetime.now(timezone.utc), True)
    pdf.cell(0, 6, f"Generated: {gen_time}", ln=True, align='C')
    pdf.cell(0, 6, time_text, ln=True, align='C')
    pdf.set_text_color(0, 0, 0)
    pdf.ln(10)

    # Device Inventory & Information Table
    pdf.set_font("helvetica", "B", 14)
    pdf.cell(0, 10, "Device Information", ln=True)
    pdf.set_font("helvetica", "", 10)
    
    # Two-column table: label | value. Long values get a full-width row (truncated if still too long).
    pdf.set_fill_color(245, 245, 245)
    info_pairs = [
        ("Device Type", data.get('device_type')),
        ("IP Address", data.get('ip_address')),
        ("Site", data.get('site')),
        ("Check Interval", f"{data.get('check_interval') or '60'}s"),
        ("Location", data.get('location')),
        ("Vendor", data.get('vendor')),
        ("Rack", data.get('rack')),
        ("Model", data.get('model')),
    ]
    fill = True
    for label, value in info_pairs:
        draw_two_column_row(pdf, label, value, fill, key_font="")
        fill = not fill
    
    pdf.ln(8)

    # Executive Summary (KPIs)
    global_uptime = data["global_uptime"]
    avg_latency = data["avg_latency"]
    avg_packet_loss = data.get("avg_packet_loss", 0.0)
    incident_count = data["incident_count"]

    pdf.set_font("helvetica", "B", 14)
    pdf.cell(0, 10, "Performance Summary", ln=True)
    
    pdf.set_font("helvetica", "", 11)
    col_width = 190 / 4
    y_before = pdf.get_y()
    
    # Box 1: Uptime
    pdf.set_xy(10, y_before)
    pdf.cell(col_width, 8, "Uptime", border=1, align='C')
    pdf.set_xy(10, y_before + 8)
    pdf.set_font("helvetica", "B", 13)
    if global_uptime >= 99.0:
        pdf.set_text_color(0, 150, 0)
    elif global_uptime >= 90.0:
        pdf.set_text_color(200, 150, 0)
    else:
        pdf.set_text_color(200, 0, 0)
    pdf.cell(col_width, 12, f"{global_uptime}%", border=1, align='C')
    pdf.set_text_color(0, 0, 0)

    # Box 2: Latency
    pdf.set_font("helvetica", "", 11)
    pdf.set_xy(10 + col_width, y_before)
    pdf.cell(col_width, 8, "Avg Latency", border=1, align='C')
    pdf.set_xy(10 + col_width, y_before + 8)
    pdf.set_font("helvetica", "B", 13)
    pdf.cell(col_width, 12, f"{avg_latency} ms", border=1, align='C')

    # Box 3: Packet Loss
    pdf.set_font("helvetica", "", 11)
    pdf.set_xy(10 + col_width*2, y_before)
    pdf.cell(col_width, 8, "Packet Loss", border=1, align='C')
    pdf.set_xy(10 + col_width*2, y_before + 8)
    pdf.set_font("helvetica", "B", 13)
    if avg_packet_loss < 2.0:
        pdf.set_text_color(0, 150, 0)
    else:
        pdf.set_text_color(200, 0, 0)
    pdf.cell(col_width, 12, f"{avg_packet_loss}%", border=1, align='C')
    pdf.set_text_color(0, 0, 0)

    # Box 4: Incidents
    pdf.set_font("helvetica", "", 11)
    pdf.set_xy(10 + col_width*3, y_before)
    pdf.cell(col_width, 8, "Incidents", border=1, align='C')
    pdf.set_xy(10 + col_width*3, y_before + 8)
    pdf.set_font("helvetica", "B", 13)
    if incident_count == 0:
        pdf.set_text_color(0, 150, 0)
    else:
        pdf.set_text_color(200, 0, 0)
    pdf.cell(col_width, 12, f"{incident_count}", border=1, align='C')
    pdf.set_text_color(0, 0, 0)

    pdf.set_y(y_before + 30)

    snmp_enabled = data.get("snmp_enabled")
    snmp_data = data.get("snmp_data")

    # Current Hardware Telemetry (SNMP)
    if snmp_enabled:
        if pdf.get_y() + 20 > 280:
            pdf.add_page()
            
        pdf.set_font("helvetica", "B", 16)
        pdf.cell(0, 10, "Current Hardware Telemetry", ln=True)
        pdf.set_font("helvetica", "I", 10)
        pdf.set_text_color(100, 100, 100)
        pdf.cell(0, 6, "Note: These metrics represent the live state of the hardware at the time of report generation.", ln=True)
        pdf.set_text_color(0, 0, 0)
        pdf.ln(2)
        
        if snmp_data and isinstance(snmp_data, dict) and len(snmp_data) > 0:
            pdf.set_font("helvetica", "", 10)
            pdf.set_fill_color(250, 250, 250)
            fill = False
            for k, v in snmp_data.items():
                clean_k = str(k).replace('_', ' ').title()
                draw_two_column_row(pdf, clean_k.strip(), v, fill, key_font="B")
                fill = not fill
        else:
            pdf.set_font("helvetica", "I", 11)
            pdf.cell(0, 8, "No SNMP metrics currently available.", ln=True)
            
        pdf.ln(8)

    # Generate Chart natively using FPDF (100% portable)
    if blocks:
        # Check if we have enough vertical space for the chart (~80 units)
        if pdf.get_y() + 80 > 280:
            pdf.add_page()
            
        pdf.set_font("helvetica", "B", 14)
        title = "Historical Uptime & Latency (ICMP)" if snmp_enabled else "Uptime & Latency Timeline"
        pdf.cell(0, 10, title, ln=True)
        
        pdf.set_font("helvetica", "I", 10)
        pdf.set_text_color(100, 100, 100)
        if time_filter == "24h":
            pdf.cell(0, 6, "Displaying hourly averages over the last 24 hours.", ln=True)
        else:
            pdf.cell(0, 6, f"Displaying block averages over the last {time_filter}.", ln=True)
        pdf.set_text_color(0, 0, 0)
        
        chart_x = 20
        chart_y = pdf.get_y() + 5
        chart_w = 160
        chart_h = 50
        
        # Draw Background Grid (0%, 25%, 50%, 75%, 100%)
        pdf.set_draw_color(240, 240, 240)
        pdf.set_line_width(0.1)
        pdf.set_font("helvetica", "", 8)
        pdf.set_text_color(150, 150, 150)
        for pct in [0, 25, 50, 75, 100]:
            y_pos = chart_y + chart_h - (chart_h * (pct / 100))
            pdf.line(chart_x, y_pos, chart_x + chart_w, y_pos)
            if pct in [0, 50, 100]:
                pdf.set_xy(chart_x - 12, y_pos - 2)
                pdf.cell(10, 4, f"{pct}%", align="R")
        
        # Calculate Latency Max for secondary axis scaling
        max_lat = max([b['latency'] for b in blocks] + [10])
        lat_scale = chart_h / max_lat
        
        # Draw Secondary Y Axis Labels (Latency)
        for pct in [0, 50, 100]:
            y_pos = chart_y + chart_h - (chart_h * (pct / 100))
            lat_val = round((pct / 100) * max_lat)
            pdf.set_xy(chart_x + chart_w + 2, y_pos - 2)
            pdf.cell(12, 4, f"{lat_val}ms", align="L")
        
        num_blocks = max(len(blocks), 1)
        bar_w = (chart_w / num_blocks) * 0.8
        step_x = chart_w / num_blocks
        
        # Draw Uptime Bars
        for i, b in enumerate(blocks):
            up = max(0, min(100, b['uptime']))
            bh = chart_h * (up / 100)
            bx = chart_x + (i * step_x) + (step_x * 0.1)
            by = chart_y + chart_h - bh
            
            if up >= 99.0:
                pdf.set_fill_color(40, 167, 69) # Green
            elif up >= 90.0:
                pdf.set_fill_color(255, 193, 7) # Yellow
            else:
                pdf.set_fill_color(220, 53, 69) # Red
                
            pdf.rect(bx, by, bar_w, bh, style='F')
        
        # Draw Latency Line
        pdf.set_draw_color(0, 123, 255) # Blue line
        pdf.set_line_width(0.6)
        prev_x = 0.0
        prev_y = 0.0
        first = True
        for i, b in enumerate(blocks):
            lat = max(0, min(max_lat, b['latency']))
            lh = lat * lat_scale
            lx = chart_x + (i * step_x) + (step_x / 2)
            ly = chart_y + chart_h - lh
            
            if not first:
                pdf.line(prev_x, prev_y, lx, ly)
            else:
                first = False
            prev_x = lx
            prev_y = ly
            
        pdf.set_line_width(0.2) # Reset to default
        pdf.set_draw_color(0, 0, 0)
        
        # Draw X-Axis Labels (Dynamic)
        pdf.set_font("helvetica", "", 7)
        pdf.set_text_color(100, 100, 100)
        
        label_step = max(1, num_blocks // 6)
        for i in range(0, num_blocks, label_step):
            b = blocks[i]
            dt = b['start']
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            dt = dt.astimezone()
            
            if time_filter == '24h':
                label = dt.strftime("%H:%M")
            else:
                label = dt.strftime("%b %d")
                
            lx = chart_x + (i * step_x) + (step_x / 2)
            pdf.set_xy(lx - 10, chart_y + chart_h + 2)
            pdf.cell(20, 4, label, align="C")
            
        pdf.set_text_color(0, 0, 0)
        pdf.set_y(chart_y + chart_h + 12)
        
        # Add Legend — capture y once to prevent drift
        legend_y = pdf.get_y()
        pdf.set_font("helvetica", "", 9)
        pdf.set_fill_color(40, 167, 69)
        pdf.rect(20, legend_y, 4, 4, style='F')
        pdf.set_xy(26, legend_y - 1)
        pdf.cell(15, 6, "99-100%")
        
        pdf.set_fill_color(255, 193, 7)
        pdf.rect(45, legend_y + 1, 4, 4, style='F')
        pdf.set_xy(51, legend_y)
        pdf.cell(15, 6, "90-98%")
        
        pdf.set_fill_color(220, 53, 69)
        pdf.rect(70, legend_y + 1, 4, 4, style='F')
        pdf.set_xy(76, legend_y)
        pdf.cell(25, 6, "<90% Uptime")
        
        pdf.set_draw_color(0, 123, 255)
        pdf.set_line_width(0.6)
        pdf.line(110, legend_y + 3, 118, legend_y + 3)
        pdf.set_xy(120, legend_y)
        pdf.set_text_color(0, 0, 0)
        pdf.cell(30, 6, "Avg Latency (ms)")
        pdf.set_line_width(0.2)
        pdf.set_draw_color(0, 0, 0)
        
        pdf.ln(10)
    else:
        pdf.cell(0, 10, "No data available for chart.", ln=True)

    pdf.ln(5)

    # Incident Log
    if pdf.get_y() + 25 > 280:
        pdf.add_page()
        
    pdf.set_font("helvetica", "B", 16)
    pdf.cell(0, 10, "Incident Log", ln=True)
    
    if not alerts:
        pdf.set_font("helvetica", "I", 12)
        pdf.set_text_color(100, 100, 100)
        pdf.cell(0, 10, "No incidents found during this period.", ln=True)
        pdf.set_text_color(0, 0, 0)
    else:
        pdf.set_font("helvetica", "B", 10)
        pdf.set_fill_color(200, 200, 200)
        pdf.cell(45, 8, "Timestamp", border=1, fill=True)
        pdf.cell(25, 8, "Type", border=1, align='C', fill=True)
        pdf.cell(120, 8, "Details", border=1, fill=True)
        pdf.ln()
        
        pdf.set_font("helvetica", "", 9)
        fill = False
        pdf.set_fill_color(245, 245, 245)
        for alert in alerts:
            ts = format_local_time(alert["timestamp"], True)
            msg = alert["message"]
            
            # Truncate message to fit 120mm column at 9pt font
            if pdf.get_string_width(msg) > 115:
                while pdf.get_string_width(msg + "...") > 115 and len(msg) > 10:
                    msg = msg[:-1]
                msg = msg + "..."

            # Timestamp cell (fixed height)
            pdf.cell(45, 7, ts, border=1, fill=fill)
            
            # Type cell with color (fixed height)
            if alert["alert_type"] == 'OFFLINE':
                pdf.set_text_color(200, 0, 0)
            elif alert["alert_type"] == 'PAUSED':
                pdf.set_text_color(220, 140, 0)
            else:
                pdf.set_text_color(0, 150, 0)
            pdf.cell(25, 7, alert["alert_type"], border=1, align='C', fill=fill)
            pdf.set_text_color(0, 0, 0)
            
            # Details cell with truncated message
            pdf.cell(120, 7, f" {msg}", border=1, fill=fill, ln=True)
            fill = not fill

    pdf.output(file_path)
