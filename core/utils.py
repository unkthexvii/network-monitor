from __future__ import annotations
from datetime import datetime, timezone, timedelta
import logging
from typing import Optional

logger = logging.getLogger(__name__)

def format_time_duration(seconds: int) -> str:
    """Formats a duration in seconds into a readable string like '1h 5m 10s'."""
    mins, secs = divmod(seconds, 60)
    if mins >= 60:
        hours, mins = divmod(mins, 60)
        return f"{hours}h {mins}m {secs}s"
    return f"{mins}m {secs}s"

def parse_utc_iso(iso_str: str) -> datetime:
    """Parses an ISO string into a timezone-naive UTC datetime object."""
    iso_str = iso_str.replace('Z', '+00:00')
    dt = datetime.fromisoformat(iso_str)
    if dt.tzinfo:
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt.astimezone().astimezone(timezone.utc).replace(tzinfo=None)

def get_timeframe_bounds(time_filter: str, start_date: Optional[str] = None, end_date: Optional[str] = None) -> tuple[datetime, datetime, int, timedelta, str]:
    """
    Returns (start_time, end_time, blocks_count, block_delta, block_label)
    based on the requested timeframe filter.
    """
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    if time_filter == 'custom' and start_date and end_date:
        try:
            start_time = parse_utc_iso(start_date)
            end_time = parse_utc_iso(end_date)
            delta = end_time - start_time
            if delta.days > 14:
                blocks_count = delta.days
                block_delta = timedelta(days=1)
                block_label = "Day"
            elif delta.days > 2:
                blocks_count = delta.days * 4
                block_delta = timedelta(hours=6)
                block_label = "6-Hour Block"
            else:
                blocks_count = int(delta.total_seconds() / 3600)
                blocks_count = max(1, blocks_count)
                block_delta = delta / blocks_count
                block_label = "Hour"
        except ValueError:
            start_time = now - timedelta(hours=24)
            end_time = now
            blocks_count = 24
            block_delta = timedelta(hours=1)
            block_label = "Hour"
    elif time_filter == '7d':
        start_time = now - timedelta(days=7)
        end_time = now
        blocks_count = 28 # 4 blocks per day (6h each) in reports, but PDF uses 7. We'll standardise to 28 for UI granularity, PDF can use it too.
        block_delta = timedelta(hours=6)
        block_label = "6-Hour Block"
    elif time_filter == '30d':
        start_time = now - timedelta(days=30)
        end_time = now
        blocks_count = 30
        block_delta = timedelta(days=1)
        block_label = "Day"
    elif time_filter == 'all':
        start_time = now - timedelta(days=30)
        end_time = now
        blocks_count = 30
        block_delta = timedelta(days=1)
        block_label = "Day"
    else: # 24h
        start_time = now - timedelta(hours=24)
        end_time = now
        blocks_count = 24
        block_delta = timedelta(hours=1)
        block_label = "Hour"
        
    return start_time, end_time, blocks_count, block_delta, block_label

def build_alert_messages(alerts: list) -> list[str]:
    """
    Batch-build alert messages in O(N) time instead of O(N²).
    Scans the list once, tracking the most recent OFFLINE/PAUSED per device_id.
    `alerts` must be sorted descending by created_at.
    Returns the same list of message strings, in the same order.
    """
    # Scan in ascending order to build a prev-map
    prev_map = {}  # device_id -> {alert_type, created_at}
    messages: list[str] = [''] * len(alerts)
    
    for i in range(len(alerts) - 1, -1, -1):
        alert = alerts[i]
        if alert.alert_type == 'OFFLINE':
            prev_map[alert.device_id] = {"type": "OFFLINE", "ts": alert.created_at}
            messages[i] = "Connection Lost"
        elif alert.alert_type == 'PAUSED':
            prev_map[alert.device_id] = {"type": "PAUSED", "ts": alert.created_at}
            messages[i] = "Monitoring Paused"
        elif alert.alert_type == 'ONLINE':
            prev = prev_map.get(alert.device_id)
            if prev and prev["type"] == "OFFLINE":
                delta = alert.created_at - prev["ts"]
                messages[i] = f"Connection Restored (Downtime: {format_time_duration(int(delta.total_seconds()))})"
            else:
                messages[i] = "Connection Restored"
            prev_map.pop(alert.device_id, None)
        elif alert.alert_type == 'RESUMED':
            prev = prev_map.get(alert.device_id)
            if prev and prev["type"] == "PAUSED":
                delta = alert.created_at - prev["ts"]
                messages[i] = f"Monitoring Resumed (Paused for: {format_time_duration(int(delta.total_seconds()))})"
            else:
                messages[i] = "Monitoring Resumed"
            prev_map.pop(alert.device_id, None)
        else:
            messages[i] = alert.message
    
    return messages

def get_default_check_interval(device_type: str) -> int:
    """Returns the default polling interval in seconds based on device type."""
    dtype = (device_type or "").lower()
    if dtype in ("router", "switch", "gateway", "firewall", "security appliance", "load balancer", "wireless controller (wlc)", "controller", "server", "hypervisor", "database", "virtual machine", "modem"):
        return 60
    elif dtype in ("access point", "ups", "pdu", "storage/nas", "ip camera", "door access control", "environmental sensor"):
        return 120
    elif dtype in ("workstation", "laptop", "thin client", "voip phone", "printer", "iot device", "device"):
        return 300
    else:
        return 60
