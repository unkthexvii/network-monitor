from __future__ import annotations
"""
Centralized configuration for the Network Monitoring System.
All tunable operational values live here. Override via environment variables.
No external dependencies — uses only Python stdlib.
"""
import os

# === Ping Engine ===
# Increased to 3 to prevent single dropped packets from marking a device offline.
PING_COUNT = int(os.getenv("MONITOR_PING_COUNT", "3"))
PING_INTERVAL = float(os.getenv("MONITOR_PING_INTERVAL", "0.2"))
PING_TIMEOUT = float(os.getenv("MONITOR_PING_TIMEOUT", "1.0"))

# === Alert Engine ===
OFFLINE_THRESHOLD = int(os.getenv("MONITOR_OFFLINE_THRESHOLD", "3"))
ONLINE_THRESHOLD = int(os.getenv("MONITOR_ONLINE_THRESHOLD", "3"))

# === Data Retention ===
RAW_PING_RETENTION_DAYS = int(os.getenv("MONITOR_RETENTION_DAYS", "7"))
MINUTE_STAT_RETENTION_DAYS = int(os.getenv("MONITOR_STAT_RETENTION_DAYS", "7"))
EVENT_HISTORY_RETENTION_DAYS = int(os.getenv("MONITOR_EVENT_RETENTION_DAYS", "90"))

# === Database ===
DATABASE_URL = os.getenv("MONITOR_DATABASE_URL", "sqlite+aiosqlite:///monitor.db")

# === Dashboard Cache ===
CACHE_TTL_SECONDS = int(os.getenv("MONITOR_CACHE_TTL", "60"))

# === Read-Only Mode ===
READONLY = os.getenv("MONITOR_READONLY", "").lower() in ("1", "true", "yes")

async def get_readonly_from_db() -> bool:
    """Read readonly flag from the Setting table (async). Falls back to False on DB error."""
    if READONLY:
        return True
    try:
        from database.models import Setting
        from database.session import async_session
        async with async_session() as session:
            row = await session.get(Setting, "monitor_readonly")
            if row:
                return row.value.lower() in ("1", "true", "yes")
    except Exception:
        pass
    return READONLY

async def set_readonly_in_db(value: bool) -> None:
    """Persist readonly flag to the Setting table."""
    from database.models import Setting
    from database.session import async_session
    async with async_session() as session:
        existing = await session.get(Setting, "monitor_readonly")
        val = "true" if value else "false"
        if existing:
            existing.value = val
        else:
            session.add(Setting(key="monitor_readonly", value=val))
        await session.commit()

# === Auth ===
DEFAULT_ADMIN_PASSWORD = os.getenv("MONITOR_DEFAULT_PASSWORD", "admin")
SESSION_TTL = int(os.getenv("MONITOR_SESSION_HOURS", "24")) * 3600
