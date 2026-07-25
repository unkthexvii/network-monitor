from __future__ import annotations
import asyncio
from sqlalchemy import event
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from .models import Base
from core.config import DATABASE_URL, DB_POOL_SIZE
import logging

logger = logging.getLogger(__name__)

engine = create_async_engine(
    DATABASE_URL,
    echo=False,
    pool_size=DB_POOL_SIZE,
    max_overflow=max(1, DB_POOL_SIZE // 3),
    connect_args={"check_same_thread": False}
)

# Apply SQLite PRAGMA for performance (WAL mode)
@event.listens_for(engine.sync_engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.execute("PRAGMA temp_store=MEMORY")
    cursor.execute("PRAGMA cache_size=-20000")
    cursor.execute("PRAGMA mmap_size=268435456") # 256MB memory mapped I/O
    cursor.execute("PRAGMA journal_size_limit=67108864") # 64MB max WAL size
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.close()

async_session = async_sessionmaker(
    engine, class_=AsyncSession, expire_on_commit=False
)

async def get_db():
    async with async_session() as session:
        yield session

async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # Auto-migration for missing columns in existing databases.
    # Column definitions are validated against a whitelist before being
    # interpolated into SQL, preventing injection if the lists are ever
    # modified to include user-supplied values.
    from sqlalchemy import text

    # Whitelist of allowed column definitions — must match the hardcoded values below.
    _ALLOWED_DEVICE_COLS = {
        "remark", "snmp_version", "snmp_community", "snmp_v3_user",
        "snmp_v3_auth", "snmp_v3_priv", "site", "location", "rack",
        "vendor", "model", "created_at", "updated_at",
    }
    _ALLOWED_STATUS_COLS = {
        "last_seen", "offline_since", "sys_name", "sys_contact",
        "sys_location", "sys_descr", "sys_uptime", "recovery_count",
        "client_count", "ap_count", "serial_number", "snmp_custom_data",
    }

    def _validate_col_def(col_def: str, allowed: set) -> str:
        col_name = col_def.split(" ")[0]
        if col_name not in allowed:
            raise ValueError(f"Column not in whitelist: {col_name!r}")
        return col_def

    columns_to_add_devices = [
        "remark VARCHAR",
        "snmp_version VARCHAR DEFAULT 'None'",
        "snmp_community VARCHAR",
        "snmp_v3_user VARCHAR",
        "snmp_v3_auth VARCHAR",
        "snmp_v3_priv VARCHAR",
        "site VARCHAR",
        "location VARCHAR",
        "rack VARCHAR",
        "vendor VARCHAR",
        "model VARCHAR",
        "created_at DATETIME",
        "updated_at DATETIME"
    ]
    columns_to_add_status = [
        "last_seen DATETIME",
        "offline_since DATETIME",
        "sys_name VARCHAR",
        "sys_contact VARCHAR",
        "sys_location VARCHAR",
        "sys_descr VARCHAR",
        "sys_uptime VARCHAR",
        "recovery_count INTEGER DEFAULT 0",
        "client_count INTEGER",
        "ap_count INTEGER",
        "serial_number VARCHAR",
        "snmp_custom_data VARCHAR"
    ]

    async with engine.begin() as conn:
        res = await conn.execute(text("PRAGMA table_info(devices)"))
        existing_cols = {row[1] for row in res.fetchall()}
        for col in columns_to_add_devices:
            col_name = col.split(" ")[0]
            if col_name not in existing_cols:
                safe_col = _validate_col_def(col, _ALLOWED_DEVICE_COLS)
                await conn.execute(text(f"ALTER TABLE devices ADD COLUMN {safe_col}"))

        res = await conn.execute(text("PRAGMA table_info(device_status)"))
        existing_cols = {row[1] for row in res.fetchall()}
        for col in columns_to_add_status:
            col_name = col.split(" ")[0]
            if col_name not in existing_cols:
                safe_col = _validate_col_def(col, _ALLOWED_STATUS_COLS)
                await conn.execute(text(f"ALTER TABLE device_status ADD COLUMN {safe_col}"))

async def dispose_engine():
    """Dispose the async engine to close all pooled connections on shutdown."""
    await engine.dispose()
