# Network Monitoring System - Backend Core Documentation

This document provides a module-by-module breakdown of the FastAPI backend application.

---

## 1. Application Entrypoint (`main.py`)

The entrypoint initializes the ASGI server and handles environment preparation, security assertions, and runtime configurations.

### Key Capabilities
- **Administrator Elevation Check:** Uses Windows system APIs (`ctypes.windll.shell32.IsUserAnAdmin()`) at boot. Raw ICMP sockets require local administrator permissions on Windows. The application exits immediately with instructions if not elevated.
- **Port Conflicts Buster:** Resolves port locking issues on restart. It scans active ports and automatically issues a `taskkill /F /PID <pid>` command if the target port (default `8000`) is in use by another process.
- **Asset Extraction (PyInstaller compatibility):** When compiled into a single executable, assets (e.g., HTML, CSS, JS, sound files) are bundled into a temporary folder (`sys._MEIPASS`). `main.py` detects if the app is frozen and extracts these files to the local execution directory for modification and persistence.
- **FastAPI Lifespan Context:** Manages startups (initializing database, loading device cache, registering SSE publishers, spinning up workers) and graceful shutdowns (shutting down scheduler threads).
- **CORS & Middleware:** Restricts cross-origin requests to localhost origins only, enforces dynamic GZip compression (skipping SSE streams to prevent buffer chunks hanging), and appends "No-Cache" HTTP headers to APIs and assets.

---

## 2. Database Module (`database/`)

The database is built on **SQLite** using **SQLAlchemy Asyncio** with the `aiosqlite` driver.

### Database Tuning (`database/session.py`)
To prevent database locking and increase write throughput for rapid polling, the connection engine enforces the following PRAGMAs:
- **Write-Ahead Logging (WAL):** Enables concurrent reads and writes.
- **Synchronous Mode = NORMAL:** Reduces disk synchronization cycles.
- **Memory Temp Storage & Cache Tuning:** Caches temporary indexes in RAM and maps up to 256MB of memory for fast reads.
- **Foreign Keys ON:** Ensures referential integrity.
- **Busy Timeout:** 5-second wait before failing on locked DB.

### Dynamic Auto-Migration
The system checks schema integrity during database initialization. It uses `PRAGMA table_info` to inspect existing columns and dynamically applies `ALTER TABLE ADD COLUMN` statements. This prevents schema drift when upgrading without corrupting user configuration. Covers `created_at`, `updated_at`, `recovery_count`, and all SNMP fields.

### Data Schema (`database/models.py`)
- **`Device`:** Hardware metadata (IP, name, device type, enabled status, remark, SNMP authentication, check interval, latency/packet-loss thresholds, timestamps).
- **`DeviceStatus`:** Real-time state. Includes latency, packet loss, fail/recovery counts, system information (sys_name, sys_descr, sys_uptime), and asset tracking (`client_count`, `ap_count`, `serial_number`).
- **`Alert`:** Alarm audit log with timestamps and status transitions (`ONLINE`/`OFFLINE`).
- **`MinuteStat`:** Per-device minute-level aggregates (avg/min/max latency, packet loss, uptime %).
- **`TopologyTab` / `TopologyNode` / `TopologyLink`:** Interactive topology mapping.
- **`Setting`:** Key-value configuration store.

---

## 3. Core Engine Module (`core/`)

The core engines orchestrate network telemetry, threshold checks, debouncing, and alert creation.

### Device Cache (`core/device_cache.py`)
- In-memory cache of all devices + their live status (`CachedDevice` dataclass).
- Loaded from DB on startup, refreshed every 30 seconds.
- Eliminates per-second DB queries during ping cycles — O(1) lookups.
- Tracks `fail_count`, `recovery_count`, `last_ping_start` for anti-flapping and backpressure.

### Workers & State Machine (`core/workers.py`)
- Coordinates ICMP ping execution in parallel chunks of 50 devices, with a global semaphore limiting concurrent chunks to 10 (500 max concurrent pings).
- Processes ping results through a state machine that applies debounce thresholds:
  - Consecutive failures increment `fail_count`; when reaching `OFFLINE_THRESHOLD` (default 3), declares OFFLINE.
  - Consecutive successes increment `recovery_count`; when reaching `ONLINE_THRESHOLD` (default 3), declares ONLINE.
- Generates Alert records and dispatches SSE events on state transitions.
- Batch-writes status updates to DB (single query, not N+1). Queries are chunked to avoid SQLite's 999-variable limit.
- Tracks `first_fail_time` per streak for accurate `offline_since` timestamps.

### ICMP Ping Engine (`core/icmp_engine.py`)
- Asynchronous ICMP ping using `icmplib` library.
- Configurable count (default 3), interval (0.2s), and timeout (1.0s).
- Returns per-IP status (ONLINE/OFFLINE), latency (ms), and packet loss.
- Windows limitation: `select()` struggles with >100 raw sockets — mitigated by chunking to 50.

### Ping Buffer (`core/ping_buffer.py`)
- Thread-safe in-memory buffer for 1-second ping samples.
- Replaces per-second DB writes — reduces DB writes by ~98%.
- Flushed every 60 seconds by the aggregator into MinuteStat rows.
- Capped at 1000 samples per device (prevents unbounded growth).

### SNMP Asset Collector (`core/snmp_engine.py`)
- A PySNMP-based client that queries hardware telemetry asynchronously.
- **Standard SNMP Query:** Retrieves `sysName`, `sysDescr`, and `sysUpTime`.
- **WLC (Wireless Controller) Metrics Query:** Queries `1.3.6.1.4.1.14179.2.1.1.1.0` (`bsnMobileStationCount`) for active wireless clients.
- **Universal Serial Number Query:** Queries `1.3.6.1.2.1.47.1.1.1.1.11.1` (`entPhysicalSerialNum`) for chassis serial numbers.
- **Custom OID Support:** Extra OIDs can be added per device type (stored in `snmp_custom_data`).
- Polls all SNMP-enabled devices every 5 minutes via the scheduler. Uses a semaphore (max 50 concurrent) for concurrency control. The 999-variable SQLite limit is handled separately in the workers/ping buffer layer, not in SNMP queries.

### Alert Engine (`core/alert_engine.py`)
- **Now a lightweight callback registry.** The actual state machine and debouncing logic lives in `workers.py:_process_ping_chunk()`.
- Provides `_notify_callback` and `register_notify_callback()` used by `api/devices.py` for pause/resume SSE notifications.

### Scheduler (`core/scheduler.py`)
- Runs background tasks using `APScheduler`.
- **Ping Poller:** Every 1 second, scans the device cache for due devices.
- **Minute Aggregator:** Every 60 seconds, flushes the ping buffer into MinuteStat records.
- **Device Cache Refresh:** Every 30 seconds, re-syncs from DB to pick up adds/removes/edits.
- **SNMP Poller:** Every 5 minutes, polls all SNMP-enabled devices.
- **WAL Checkpoint:** Every 5 minutes, truncates the SQLite WAL to prevent unbounded growth.
- **Daily Cleanup:** Archives old data (raw pings >7d, stats >7d, alerts >90d) into monthly databases. Archive queries use parameterized SQL and validated identifiers.
- **Weekly VACUUM:** Reclaims disk space after cleanup deletes.
- **Hourly Log Purge:** Removes rotated log backups older than 24 hours.

### Additional Modules
- **`core/config.py`:** All tunable constants with environment variable overrides.
- **`core/utils.py`:** Timeframe bounds, alert message building (O(N)), default check intervals.
- **`core/pagination.py`:** Reusable server-side pagination for SQLAlchemy queries.
- **`core/cache.py`:** `TTLCache` — bounded (500 entries) in-memory cache with per-key expiry and expired-first eviction.

---

## 4. API Endpoints (`api/`)

FastAPI handles REST requests and streams Server-Sent Events.

- **`devices.py`**: CRUD devices (`GET`, `POST`, `PUT`, `DELETE` `/api/devices`). LAN discovery (`/api/devices/discover`), CSV export, paginated listing with status/search/subnet filters. Input validation via Pydantic validators (IP address, check interval, CIDR subnet). SNMP test endpoint (`/api/test/snmp/{device_id}`) with masked community string.
- **`auth.py`**: Authentication endpoints (`POST /api/auth/login`, `POST /api/auth/logout`, `GET /api/auth/check`, `POST /api/auth/change-password`). Password hashing via salted SHA-256, httpOnly cookie session store with configurable TTL. Default password set via `MONITOR_DEFAULT_PASSWORD` env var on first run.
- **`dashboard.py`**: Network health statistics, recent events feed (paginated, 24h window), offline device listing.
- **`alerts.py`**: Fetches paginated alert history with time/status/device filtering. Supports grouped-by-device or per-device views.
- **`topology.py`**: CRUD for topology tabs, nodes (positioned devices), and links (connections between devices). Icon type mapping by device category.
- **`reports.py`**: Generate and download performance PDF reports. UI data endpoint for report previews.
- **`stream.py`**: SSE stream for real-time updates. Uses `dict[id, {queue, last_activity}]` for client tracking with 120s timeout and stale client cleanup.

---

## 5. Reports Engine (`reporting/pdf_generator.py`)

Compiles professional-grade network health summaries.

- Uses the **fpdf2** library to build PDF files.
- **Visual Formatting:** Implements dynamic headers, page numbers, clean table layouts, and color-coded cell highlights based on status.
- **Company Branding:** Dynamically searches the `/logo` folder next to the executable. If a company logo is found (e.g. `.png` or `.jpg`), it embeds it into the PDF header automatically.
- **Content:** Includes an executive summary, device inventory table (with serial numbers and WLC client counts), average latency trends, and a complete history of network alerts during the reporting window.

---

## 6. Additional Endpoints

### Logo and Favicon
- **`/api/logo`** (`GET`): Serves the first image file found in the `logo/` directory next to the executable. Supports PNG, JPG, JPEG, GIF, SVG, and WebP formats. Returns 404 if no logo is found.
- **`/favicon.ico`** (`GET`): Serves the logo as a favicon. Returns 404 if no logo is found.

### Read-Only Mode
- **`/api/readonly`** (`GET`): Returns the current readonly status and whether the client is authenticated. No authentication required.
- **`/api/admin/readonly`** (`POST`): Toggles readonly mode. Requires authentication. When enabled, all mutating API requests (POST/PUT/DELETE) return HTTP 403.
- Readonly mode can be set via the `MONITOR_READONLY` environment variable or via the API.

### Crash Handler
- The application installs a global exception handler (`sys.excepthook`) that writes unhandled exception tracebacks to `crash.log` in the application directory. This file is useful for debugging startup crashes and unhandled exceptions.
