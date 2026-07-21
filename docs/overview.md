# Network Monitoring System - Architecture Overview

This document provides a high-level overview of the system architecture, directory structure, communication protocols, and startup lifecycles of the Network Monitoring System.

---

## 1. System Architecture

The Network Monitoring System is a real-time, lightweight network surveillance tool designed to run efficiently on Windows systems (including legacy targets like Windows 7). It is structured as a **Single Page Application (SPA)** frontend communicating with a **FastAPI (Python)** backend, backed by an **SQLite** database with Write-Ahead Logging (WAL) enabled.

```mermaid
graph TD
    subgraph Frontend [SPA Frontend]
        UI[index.html / CSS / BootStrap]
        JS[app.js - State & Live UI Updaters]
        Audio[Audio Alarm Engine - online.mp3 / offline.mp3]
    end

    subgraph Backend [FastAPI Backend Server]
        API[FastAPI Routers - Devices, Alerts, Dashboard, Reports, Stream]
        Cache[In-Memory Device Cache, Ping Buffer & TTLCache (bounded)]
        Workers[Background Threads - ICMP Engine & SNMP Engine]
        Alerts[Alert Engine - Debouncer / Evaluator]
        Sched[Scheduler - Stats Roll-up, DB Cleanup & VACUUM]
        ReportGen[PDF Report Generator - fpdf2]
    end

    subgraph Storage [SQLite Database]
        DB[(monitor.db)]
    end

    UI <-->|HTTP / REST API| API
    JS <---|Server-Sent Events / SSE Stream| API
    API <-->|SQLAlchemy Async| DB
    Workers -->|DB Status Writes| DB
    Workers -->|Cache Sync| Cache
    Cache -->|Metrics Evaluation| Alerts
    Alerts -->|SSE Events| API
    Sched <-->|Hour/Day Aggregations| DB
```

---

## 2. Directory Structure

```
network-monitor/
│
├── main.py                    # Application Entrypoint (FastAPI, Lifespan, Win7 Privilege Check)
├── migrate.py                 # Standalone/Legacy SQLite Migration Helper
├── NetworkMonitor.spec        # PyInstaller Spec for Modern Windows builds
├── NetworkMonitor_Win7.spec   # PyInstaller Spec for Windows 7 builds
├── build.bat / build_win7.bat # Compile scripts (compiles Python code to standalone .exe)
│
├── api/                       # API Routers & Endpoints
│   ├── alerts.py              # Get alerts, filter alert events
│   ├── dashboard.py           # Metrics, latency stats, uptime summary
│   ├── devices.py             # Device CRUD, monitoring toggle
│   ├── reports.py             # PDF Report triggers & downloads
│   ├── stream.py              # Server-Sent Events (SSE) Real-time Stream
│   └── topology.py            # Nodes & Links configurations
│
├── core/                      # Core Monitoring Engines & Utilities
│   ├── alert_engine.py        # SSE callback registry (state machine in workers.py)
│   ├── cache.py               # Shared Memory Cache base
│   ├── config.py              # System constants (Thresholds, intervals, paths)
│   ├── device_cache.py        # Active devices cached in-memory
│   ├── icmp_engine.py         # Subprocess ICMP ping runner
│   ├── pagination.py          # Paginated query helper
│   ├── ping_buffer.py         # Thread-safe buffer for ping history
│   ├── scheduler.py           # Stats aggregation scheduler & cleanup jobs
│   ├── snmp_engine.py         # PySNMP client (SysInfo, WLC Client Count, Serial Numbers)
│   ├── utils.py               # Network helpers (IP validation)
│   └── workers.py             # ICMP/SNMP background worker threads
│
├── database/                  # Database Layer
│   ├── models.py              # SQLAlchemy Schema Models
│   ├── repository.py          # Database operations abstraction layer
│   └── session.py             # DB connection pool, SQLite WAL optimization, Auto-Migrations
│
├── reporting/                 # Reports Module
│   └── pdf_generator.py       # ReportLab PDF building engine
│
├── static/                    # Frontend Static Files
│   ├── index.html             # UI Structure (Bootstrap, Icons, Custom Cards)
│   ├── app.js                 # Frontend Controller (AJAX, State, SSE, Chart.js, Topology map)
│   ├── style.css              # Custom styling sheet (Dark mode theme, Indicator animations)
│   ├── online.mp3             # Playable sound when device recovers
│   └── offline.mp3            # Playable sound when device crashes
│
└── logo/                      # Custom Logo Directory (Used for PDF headers & UI branding)
```

---

## 3. Communication Protocols

### REST API (HTTP)
Used for transactional operations such as creating/editing/deleting devices, downloading PDF reports, changing application settings, and loading historical chart data.

### Server-Sent Events (SSE)
Instead of polling the backend constantly or utilizing complex WebSockets, the frontend establishes a unidirectional Server-Sent Event stream via the `/api/stream` endpoint.
- Whenever a background ping completes, the latency and status details are pushed down the SSE stream.
- Whenever a device status transitions (e.g., transitions to `OFFLINE` or recovers back to `ONLINE`), an alert event is instantly pushed down the stream, triggering the frontend to update cards, flash grids, and trigger sound alarms.

---

## 4. Application Lifespan & Startup Flow

When `main.py` is executed:
1. **Windows Administrator Privilege Check:** Asynchronous ICMP pinging requires raw sockets or access to diagnostic APIs. On Windows, this requires running with Administrator privileges. The application validates this using `ctypes.windll.shell32.IsUserAnAdmin()` and terminates immediately with an error log if not elevated.
2. **Database Initialization & Migrations:** `init_db()` is invoked. It generates the database tables if they do not exist. It then uses `PRAGMA table_info` to inspect the table schema and dynamically adds any missing columns (e.g., `client_count`, `serial_number`, `remark`) without corrupting existing data.
3. **Cache Hydration:** Active, enabled devices are loaded from the database into the in-memory `device_cache`.
4. **SSE Callbacks Registration:** The event broadcast queues are bound to the SSE dispatch registry.
5. **Scheduler & Worker Startup:** The aggregation scheduler starts running. Simultaneously, background worker threads are spawned to start polling network hardware.
6. **Web Server Initialization:** FastAPI starts serving the web interface via Uvicorn on port `8000` (or `MONITOR_PORT`).
