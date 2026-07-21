from __future__ import annotations
import asyncio
import logging
from datetime import datetime, timezone, timedelta
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import select
from database.session import async_session
from database.models import Device, MinuteStat
from core.workers import ping_worker
from core.config import OFFLINE_THRESHOLD, ONLINE_THRESHOLD
from core.ping_buffer import ping_buffer
from core.device_cache import device_cache
import time

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler()

# A simple counter to manage check intervals.
# If tick % device.check_interval == 0, the device is due for a ping.
tick = 0

def _handle_task_exception(task: asyncio.Task):
    """Callback attached to fire-and-forget tasks to log exceptions."""
    try:
        exc = task.exception()
        if exc:
            logger.error(f"Background task failed: {exc}", exc_info=exc)
    except asyncio.CancelledError:
        pass

async def schedule_pings():
    global tick
    tick += 1

    # Read device list from in-memory cache (ZERO database queries)
    enabled = device_cache.get_enabled_devices()

    due_devices = []
    now = time.monotonic()
    for cached_dev in enabled:
        # Per-device backpressure: prevent overlapping ping cycles for the SAME device.
        # If last_ping_start > 0, a ping cycle is in progress or was orphaned.
        # Allow re-ping if elapsed > 60s (catches orphaned tasks without stalling permanently).
        elapsed = now - cached_dev.last_ping_start
        if cached_dev.last_ping_start > 0 and elapsed < 60.0:
            continue

        interval = cached_dev.check_interval

        # Fast Retry Mode (Smart Debounce)
        if (0 < cached_dev.fail_count < OFFLINE_THRESHOLD) or (0 < cached_dev.recovery_count < ONLINE_THRESHOLD):
            interval = 30

        # Spread out ping load across the interval to prevent "thundering herd" CPU spikes
        # when hundreds of devices share the exact same interval.
        offset = cached_dev.id % interval
        
        # For UNKNOWN devices (e.g. after mass import), use a minimum 10s interval
        # instead of pinging every 1-second tick, to prevent a ping storm.
        effective_interval = interval
        if cached_dev.status == "UNKNOWN":
            effective_interval = max(interval, 10)
        
        if (tick + offset) % effective_interval == 0:
            cached_dev.last_ping_start = time.monotonic()
            due_devices.append(cached_dev)

    if due_devices:
        # Fire and forget! The ping_worker will clear the is_pinging flag when done.
        asyncio.create_task(ping_worker(due_devices))


async def aggregate_minute_stats():
    """
    Aggregate ping data from the in-memory PingBuffer into minute_stats.
    Runs every 60 seconds.

    This replaces the old RawPing-based SQL aggregation with a pure-RAM
    computation, reducing database load by ~98%.
    """
    try:
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        minute_start = now.replace(second=0, microsecond=0) - timedelta(minutes=1)

        # Atomically grab and clear the buffer
        snapshot = await ping_buffer.flush()

        if not snapshot:
            return

        async with async_session() as session:
            # Get check_interval from cache (no DB query needed)
            stats_to_add = []
            for device_id, samples in snapshot.items():
                if not samples:
                    continue

                total = len(samples)
                online = sum(1 for s in samples if s.status == "ONLINE")
                offline = total - online

                # Single-pass computation — avoids 2 intermediate lists and 4 traversals
                total_lat = 0.0
                count_lat = 0
                min_latency = float('inf')
                max_latency = 0.0
                total_loss = 0.0
                count_loss = 0
                for s in samples:
                    if s.latency_ms is not None and s.latency_ms > 0:
                        if count_lat == 0:
                            min_latency = s.latency_ms
                            max_latency = s.latency_ms
                        else:
                            if s.latency_ms < min_latency:
                                min_latency = s.latency_ms
                            if s.latency_ms > max_latency:
                                max_latency = s.latency_ms
                        total_lat += s.latency_ms
                        count_lat += 1
                    if s.packet_loss is not None:
                        total_loss += s.packet_loss
                        count_loss += 1

                avg_latency = total_lat / count_lat if count_lat else 0.0
                avg_loss = total_loss / count_loss if count_loss else 0.0
                if count_lat == 0:
                    min_latency = 0.0

                cached_dev = device_cache.get_device(device_id)
                check_interval = cached_dev.check_interval if cached_dev else 60

                # Uptime calculation (debounce-aware)
                if total == 0:
                    uptime_pct = 0.0
                elif online == 0:
                    uptime_pct = 0.0
                elif offline == 0:
                    uptime_pct = 100.0
                else:
                    fast_retry_fails = min(offline, OFFLINE_THRESHOLD)
                    normal_fails = max(0, offline - OFFLINE_THRESHOLD)
                    # During debounce, fast retry interval is 30s. So each failure represents 30s of downtime.
                    downtime_seconds = (fast_retry_fails * 30) + (normal_fails * check_interval)
                    downtime_seconds = min(downtime_seconds, 60)
                    uptime_pct = max(0.0, (60 - downtime_seconds) / 60 * 100)

                stats_to_add.append(MinuteStat(
                    device_id=device_id,
                    minute=minute_start,
                    avg_latency=avg_latency,
                    min_latency=min_latency,
                    max_latency=max_latency,
                    packet_loss=avg_loss,
                    uptime_percent=uptime_pct,
                ))

            # Bulk insert all MinuteStat records in one commit
            if stats_to_add:
                session.add_all(stats_to_add)
                await session.commit()
                logger.info(f"Aggregated minute stats for {len(stats_to_add)} devices (from RAM)")

    except Exception as e:
        logger.error(f"aggregate_minute_stats failed: {e}", exc_info=True)


async def refresh_device_cache():
    """Periodically refresh device cache from DB to pick up adds/removes."""
    try:
        await device_cache.refresh_from_db()
    except Exception as e:
        logger.error(f"Device cache refresh failed: {e}", exc_info=True)


async def cleanup_old_data():
    """Archive old records into monthly databases before deleting to prevent unbounded database growth."""
    import calendar
    import os
    import aiosqlite
    from core.config import DATABASE_URL, RAW_PING_RETENTION_DAYS, MINUTE_STAT_RETENTION_DAYS, EVENT_HISTORY_RETENTION_DAYS

    # Whitelist of allowed table/column names — must match the hardcoded values below.
    # These are interpolated into SQL via f-strings, so they MUST stay in this list.
    _ALLOWED_TABLES = {"raw_ping", "minute_stats", "alerts"}
    _ALLOWED_COLS = {"ts", "minute", "created_at"}

    def _validate_ident(name: str, allowed: set) -> str:
        if name not in allowed:
            raise ValueError(f"Identifier not in whitelist: {name!r}")
        return name

    archive_dir = os.path.join(os.getcwd(), "archives")
    os.makedirs(archive_dir, exist_ok=True)
    
    db_path = DATABASE_URL.replace("sqlite+aiosqlite:///", "")
    if not db_path:
        db_path = "monitor.db"
        
    try:
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        
        tables_to_archive = [
            ("raw_ping", now - timedelta(days=RAW_PING_RETENTION_DAYS), "ts"),
            ("minute_stats", now - timedelta(days=MINUTE_STAT_RETENTION_DAYS), "minute"),
            ("alerts", now - timedelta(days=EVENT_HISTORY_RETENTION_DAYS), "created_at")
        ]
        
        async with aiosqlite.connect(db_path) as db:
            for table_name, cutoff, time_col in tables_to_archive:
                _validate_ident(table_name, _ALLOWED_TABLES)
                _validate_ident(time_col, _ALLOWED_COLS)
                async with db.execute(f"SELECT count(*) FROM sqlite_master WHERE type='table' AND name=?", (table_name,)) as cursor:
                    if (await cursor.fetchone())[0] == 0:
                        continue
                    
                cutoff_str = cutoff.strftime("%Y-%m-%d %H:%M:%S")
                
                while True:
                    async with db.execute(f"SELECT MIN(strftime('%Y-%m', {time_col})) FROM {table_name} WHERE {time_col} < ?", (cutoff_str,)) as cursor:
                        year_month = (await cursor.fetchone())[0]
                    
                    if not year_month:
                        break
                        
                    year, month = map(int, year_month.split('-'))
                    _, last_day = calendar.monthrange(year, month)
                    
                    month_start_str = f"{year}-{month:02d}-01 00:00:00"
                    
                    month_end = datetime(year, month, last_day, 23, 59, 59, 999999, tzinfo=timezone.utc)
                    effective_end = min(month_end, cutoff)
                    effective_end_str = effective_end.strftime("%Y-%m-%d %H:%M:%S")
                    
                    archive_db_path = os.path.join(archive_dir, f"archive_{year}_{month:02d}.db")
                    archive_db_path_sql = archive_db_path.replace("'", "''")
                    
                    await db.execute(f"ATTACH DATABASE '{archive_db_path_sql}' AS archive")
                    
                    await db.execute(f"CREATE TABLE IF NOT EXISTS archive.{table_name} AS SELECT * FROM main.{table_name} WHERE 0")
                    
                    await db.execute(
                        f"INSERT INTO archive.{table_name} SELECT * FROM main.{table_name} WHERE {time_col} >= ? AND {time_col} <= ?",
                        (month_start_str, effective_end_str)
                    )
                    
                    await db.execute(
                        f"DELETE FROM main.{table_name} WHERE {time_col} >= ? AND {time_col} <= ?",
                        (month_start_str, effective_end_str)
                    )
                    
                    await db.commit()
                    await db.execute("DETACH DATABASE archive")
                    
                    logger.info(f"Archived {table_name} for {year}-{month:02d}")
                    
    except Exception as e:
        logger.error(f"Database archiving failed: {e}", exc_info=True)


async def run_wal_checkpoint():
    """Periodic WAL truncation to prevent unbounded growth."""
    from sqlalchemy import text
    try:
        async with async_session() as session:
            await session.execute(text("PRAGMA wal_checkpoint(PASSIVE)"))
            logger.debug("Executed SQLite WAL checkpoint (PASSIVE)")
    except Exception as e:
        logger.error(f"WAL checkpoint failed: {e}", exc_info=True)


async def vacuum_db():
    """Weekly VACUUM to reclaim disk space after cleanup deletes old rows."""
    from sqlalchemy import text
    try:
        async with async_session() as session:
            await session.execute(text("PRAGMA optimize"))
            await session.execute(text("VACUUM"))
            logger.info("Database vacuum completed (space reclaimed)")
    except Exception as e:
        logger.error(f"Database vacuum failed: {e}", exc_info=True)


async def purge_logs():
    """Remove rotated log backups older than 24 hours and stale crash.log."""
    LOG_CLEANUP_HOURS = 24
    import os
    import time as time_module

    try:
        cutoff = time_module.time() - (LOG_CLEANUP_HOURS * 3600)
        log_dir = os.path.join(os.getcwd(), "logs")
        removed = 0
        freed = 0

        # Clean rotated log backups in logs/ dir (app.log.1, snmp_failures.log.2, etc.)
        if os.path.isdir(log_dir):
            with os.scandir(log_dir) as entries:
                for entry in entries:
                    if not entry.is_file():
                        continue
                    name = entry.name
                    # Matches rotated backups: <name>.log.<N> where N is a number
                    if ".log." in name:
                        parts = name.split(".log.")
                        if len(parts) == 2 and parts[1].isdigit():
                            try:
                                stat = entry.stat()
                                if stat.st_mtime < cutoff:
                                    sz = stat.st_size
                                    os.remove(entry.path)
                                    removed += 1
                                    freed += sz
                            except OSError:
                                pass

        # Clean crash.log from working directory
        crash_path = os.path.join(os.getcwd(), "crash.log")
        if os.path.isfile(crash_path):
            try:
                stat = os.stat(crash_path)
                if stat.st_mtime < cutoff:
                    sz = stat.st_size
                    os.remove(crash_path)
                    removed += 1
                    freed += sz
            except OSError:
                pass

        if removed:
            logger.info(f"Log purge: removed {removed} file(s), freed {freed / 1024:.1f} KB")
        else:
            logger.debug("Log purge: no stale files found")

    except Exception as e:
        logger.error(f"Log purge failed: {e}", exc_info=True)


def start_scheduler():
    from core.snmp_engine import poll_all_devices
    
    # Schedule the ping polling every 1 second
    scheduler.add_job(schedule_pings, 'interval', seconds=1, id='ping_poller')

    # Schedule aggregation every 1 minute
    scheduler.add_job(aggregate_minute_stats, 'interval', minutes=1, id='minute_aggregator')

    # Refresh device cache every 30 seconds
    scheduler.add_job(refresh_device_cache, 'interval', seconds=30, id='device_cache_refresh')

    # Truncate WAL every 5 minutes
    scheduler.add_job(run_wal_checkpoint, 'interval', minutes=5, id='wal_checkpoint')
    
    # SNMP Polling every 5 minutes
    scheduler.add_job(poll_all_devices, 'interval', minutes=5, id='snmp_poller')

    # Daily database cleanup
    scheduler.add_job(cleanup_old_data, 'interval', days=1, id='db_cleanup')

    # Weekly vacuum to reclaim disk space
    scheduler.add_job(vacuum_db, 'interval', weeks=1, id='db_vacuum')

    # Hourly log file cleanup (rotated backups older than 24h)
    scheduler.add_job(purge_logs, 'interval', hours=1, id='log_purge')

    scheduler.start()
    logger.info("Scheduler started: ping_poller (1s), minute_aggregator (1m), cache_refresh (30s), wal_checkpoint (5m), snmp_poller (5m), db_vacuum (1w), log_purge (1h)")

def shutdown_scheduler():
    scheduler.shutdown()
    logger.info("Scheduler shut down")
