from __future__ import annotations
import os
import re
import aiosqlite
import logging
from datetime import datetime
from core.config import DATABASE_URL

logger = logging.getLogger(__name__)

def parse_db_date(date_str: str) -> datetime:
    # Strip Z suffix (Python 3.8 fromisoformat() doesn't support it)
    date_str = date_str.replace('Z', '+00:00')
    if 'T' in date_str:
        return datetime.fromisoformat(date_str).replace(tzinfo=None)
    if '.' in date_str:
        return datetime.strptime(date_str, "%Y-%m-%d %H:%M:%S.%f").replace(tzinfo=None)
    return datetime.strptime(date_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=None)

async def _get_federated_db_connection(start_time: datetime, end_time: datetime) -> tuple[aiosqlite.Connection, list[str]]:
    db_path = DATABASE_URL.replace("sqlite+aiosqlite:///", "")
    if not db_path:
        db_path = "monitor.db"
    
    # Connect directly to the main database — eliminates the fragile
    # in-memory + ATTACH pattern that caused "database disk image is malformed"
    # errors when a second concurrent connection was opened in WAL mode.
    db = await aiosqlite.connect(db_path)

    try:
        # Archive directory is relative to the database directory
        db_dir = os.path.dirname(db_path)
        archive_dir = os.path.join(db_dir, "archives")

        start_year_month = (start_time.year, start_time.month)
        end_year_month = (end_time.year, end_time.month)
        
        # Validate archive file path to prevent SQL injection via ATTACH DATABASE
        _archive_path_re = re.compile(r'^archive_\d{4}_\d{2}\.db$')

        # Resolve and validate the archive directory to prevent path traversal
        archive_dir = os.path.abspath(archive_dir)

        attached_dbs = []
        curr_y, curr_m = start_year_month
        while (curr_y, curr_m) <= end_year_month:
            arch_filename = f"archive_{curr_y}_{curr_m:02d}.db"
            arch_path = os.path.abspath(os.path.join(archive_dir, arch_filename))
            # Only ATTACH files matching the expected archive pattern and within the archive dir
            if arch_path.startswith(archive_dir + os.sep) and _archive_path_re.match(arch_filename) and os.path.exists(arch_path):
                db_name = f"arch_{curr_y}_{curr_m:02d}"
                # Escape single quotes in path for SQL safety
                safe_path = arch_path.replace("'", "''")
                await db.execute(f"ATTACH DATABASE '{safe_path}' AS \"{db_name}\"")
                attached_dbs.append(db_name)
            
            curr_m += 1
            if curr_m > 12:
                curr_m = 1
                curr_y += 1
                
        return db, attached_dbs
    except Exception as e:
        await db.close()
        raise


# ── Internal query helpers (accept a shared connection, don't close it) ──

async def _query_stats_aggregates(db: aiosqlite.Connection, attached_dbs: list, device_id: int, start_time: datetime, end_time: datetime):
    main_query = "SELECT avg_latency, uptime_percent, packet_loss FROM minute_stats WHERE device_id = ? AND minute >= ? AND minute <= ?"
    params = [device_id, start_time.strftime("%Y-%m-%d %H:%M:%S"), end_time.strftime("%Y-%m-%d %H:%M:%S")]
    
    all_queries = [main_query]
    for attached in attached_dbs:
        # Safe: attached names come from _get_federated_db_connection which validates the path regex
        all_queries.append(f"SELECT avg_latency, uptime_percent, packet_loss FROM \"{attached}\".minute_stats WHERE device_id = ? AND minute >= ? AND minute <= ?")
    
    full_query = f"SELECT AVG(avg_latency), AVG(uptime_percent), AVG(packet_loss) FROM ({' UNION ALL '.join(all_queries)})"
    
    async with db.execute(full_query, params * len(all_queries)) as cursor:
        row = await cursor.fetchone()
        return {
            "avg_latency": row[0] if row and row[0] is not None else 0.0,
            "avg_uptime": row[1] if row and row[1] is not None else 100.0,
            "avg_packet_loss": row[2] if row and row[2] is not None else 0.0
        }


async def _query_incident_count(db: aiosqlite.Connection, attached_dbs: list, device_id: int, start_time: datetime, end_time: datetime):
    main_query = "SELECT COUNT(id) FROM alerts WHERE device_id = ? AND created_at >= ? AND created_at <= ? AND alert_type = 'OFFLINE'"
    params = [device_id, start_time.strftime("%Y-%m-%d %H:%M:%S"), end_time.strftime("%Y-%m-%d %H:%M:%S")]
    
    all_queries = [main_query.replace('COUNT(id)', 'COUNT(id) AS c')]
    for attached in attached_dbs:
        all_queries.append(f"SELECT COUNT(id) AS c FROM \"{attached}\".alerts WHERE device_id = ? AND created_at >= ? AND created_at <= ? AND alert_type = 'OFFLINE'")
    
    full_query = f"SELECT SUM(c) FROM ({' UNION ALL '.join(all_queries)})"
    
    async with db.execute(full_query, params * len(all_queries)) as cursor:
        row = await cursor.fetchone()
        return row[0] if row and row[0] is not None else 0


async def _query_minute_stats(db: aiosqlite.Connection, attached_dbs: list, device_id: int, start_time: datetime, end_time: datetime):
    main_query = "SELECT device_id, minute, avg_latency, min_latency, max_latency, packet_loss, uptime_percent FROM minute_stats WHERE device_id = ? AND minute >= ? AND minute <= ?"
    params = [device_id, start_time.strftime("%Y-%m-%d %H:%M:%S"), end_time.strftime("%Y-%m-%d %H:%M:%S")]
    
    all_queries = [main_query]
    for attached in attached_dbs:
        all_queries.append(f"SELECT device_id, minute, avg_latency, min_latency, max_latency, packet_loss, uptime_percent FROM \"{attached}\".minute_stats WHERE device_id = ? AND minute >= ? AND minute <= ?")
    
    full_query = f"SELECT * FROM ({' UNION ALL '.join(all_queries)}) ORDER BY minute ASC"
    
    stats = []
    async with db.execute(full_query, params * len(all_queries)) as cursor:
        async for row in cursor:
            stats.append(MinuteStatRow(row))
    return stats


async def _query_alerts(db: aiosqlite.Connection, attached_dbs: list, device_id: int, start_time: datetime, end_time: datetime, limit: int = 50):
    main_query = "SELECT id, device_id, alert_type, message, created_at FROM alerts WHERE device_id = ? AND created_at >= ? AND created_at <= ?"
    params = [device_id, start_time.strftime("%Y-%m-%d %H:%M:%S"), end_time.strftime("%Y-%m-%d %H:%M:%S")]
    
    all_queries = [main_query]
    for attached in attached_dbs:
        all_queries.append(f"SELECT id, device_id, alert_type, message, created_at FROM \"{attached}\".alerts WHERE device_id = ? AND created_at >= ? AND created_at <= ?")
    
    full_query = f"SELECT * FROM ({' UNION ALL '.join(all_queries)}) ORDER BY created_at DESC LIMIT ?"
    
    alerts = []
    all_params = params * len(all_queries) + [limit]
    async with db.execute(full_query, all_params) as cursor:
        async for row in cursor:
            alerts.append(AlertRow(row))
    return alerts


# ── Public single-query functions (each opens its own connection) ──

async def get_federated_stats_aggregates(device_id: int, start_time: datetime, end_time: datetime):
    db, attached_dbs = await _get_federated_db_connection(start_time, end_time)
    try:
        return await _query_stats_aggregates(db, attached_dbs, device_id, start_time, end_time)
    finally:
        await db.close()

async def get_federated_incident_count(device_id: int, start_time: datetime, end_time: datetime):
    db, attached_dbs = await _get_federated_db_connection(start_time, end_time)
    try:
        return await _query_incident_count(db, attached_dbs, device_id, start_time, end_time)
    finally:
        await db.close()

async def get_federated_minute_stats(device_id: int, start_time: datetime, end_time: datetime):
    db, attached_dbs = await _get_federated_db_connection(start_time, end_time)
    try:
        return await _query_minute_stats(db, attached_dbs, device_id, start_time, end_time)
    finally:
        await db.close()

async def get_federated_alerts(device_id: int, start_time: datetime, end_time: datetime, limit: int = 50):
    db, attached_dbs = await _get_federated_db_connection(start_time, end_time)
    try:
        return await _query_alerts(db, attached_dbs, device_id, start_time, end_time, limit)
    finally:
        await db.close()


# ── Batch function: one connection, all queries ──

async def get_federated_report_data(device_id: int, start_time: datetime, end_time: datetime, alert_limit: int = 50):
    """Fetch stats, incident count, alerts, and minute stats over a shared connection.
    Eliminates 3 redundant DB connection + ATTACH cycles compared to calling individual functions."""
    db, attached_dbs = await _get_federated_db_connection(start_time, end_time)
    try:
        stats = await _query_stats_aggregates(db, attached_dbs, device_id, start_time, end_time)
        incidents = await _query_incident_count(db, attached_dbs, device_id, start_time, end_time)
        alerts = await _query_alerts(db, attached_dbs, device_id, start_time, end_time, alert_limit)
        minute_stats = await _query_minute_stats(db, attached_dbs, device_id, start_time, end_time)
        return stats, incidents, alerts, minute_stats
    finally:
        await db.close()


class MinuteStatRow:
    def __init__(self, row):
        self.device_id = row[0]
        self.minute = parse_db_date(row[1])
        self.avg_latency = row[2]
        self.min_latency = row[3]
        self.max_latency = row[4]
        self.packet_loss = row[5]
        self.uptime_percent = row[6]

class AlertRow:
    def __init__(self, row):
        self.id = row[0]
        self.device_id = row[1]
        self.alert_type = row[2]
        self.message = row[3]
        self.created_at = parse_db_date(row[4])
