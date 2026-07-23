from __future__ import annotations
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# Notify callback for SSE event dispatch.
# Registered by main.py at startup via register_notify_callback.
_notify_callback = None


def register_notify_callback(cb):
    """Register the SSE publisher callback for state-change notifications."""
    global _notify_callback
    _notify_callback = cb


async def notify_state_change(old_status: str, new_status: str, dev) -> None:
    """
    Dispatch a state-change SSE event to connected clients.
    Uses the registered callback (typically sse_publisher from api/stream.py).
    Must be called AFTER the DB commit so clients only see persisted state.
    """
    if not _notify_callback:
        return

    message, event_type = build_alert_message(old_status, new_status, dev)

    event_data = {
        "device_id": dev.id,
        "device_name": dev.name,
        "ip_address": dev.ip_address,
        "status": event_type,
        "message": message,
        "timestamp": datetime.now(timezone.utc).isoformat()
    }
    try:
        await _notify_callback("status_change", event_data)
    except Exception as e:
        logger.warning(f"SSE notify failed for device {dev.id}: {e}")


def build_alert_message(old_status: str, new_status: str, dev) -> tuple[str, str]:
    """
    Build the alert message and event type for a state transition.
    Shared by both the Alert DB record creation and the SSE event dispatch
    to ensure consistency.

    Returns (message, alert_type).
    """
    if old_status == "UNKNOWN" and new_status == "ONLINE":
        return f"Device Initialized: {dev.name} ({dev.ip_address})", "INITIALIZED"
    elif new_status in ("PAUSED", "RESUMED"):
        return f"Monitoring for device {dev.name} ({dev.ip_address}) was {new_status.lower()}.", new_status
    else:
        return f"Device {dev.name} ({dev.ip_address}) went {new_status}.", new_status
