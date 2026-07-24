# Network Monitoring System Documentation

Welcome to the documentation for the Network Monitoring System. This system monitors local network endpoints using asynchronous ICMP pinging and SNMP telemetry, storing performance metrics and alerting on status changes.

---

## Documentation Index

- **[Quick Start / Setup Guide](../README.md)** — Getting started, configuration, and building
- [1. Architecture Overview](overview.md)
  - Broad system architecture and directory layouts.
  - Inter-process communication logic (REST and SSE streams).
  - Main application lifespan events and Windows startup checks.
  - **Mermaid architecture diagrams** showing component relationships.

- [2. Frontend UI Documentation](frontend.md)
  - HTML structure and dark NOC style system.
  - SPA controller logic (`app.js`), page routing, and SSE listener.
  - Dashboard analytics, interactive topology map, and sound alarm systems.
  - Details panel showing real-time SNMP SysInfo and SNMP Asset data (Connected Client Counts / Hardware Serial Numbers).

- [3. Backend Core Documentation](backend.md)
  - Initialization flows, elevation guards, and port conflicts checks.
  - Database schema models and performance-tuned SQLite WAL configs.
  - ICMP and SNMP background worker threads.
  - Anti-flapping alert state debouncer.
  - API routers and ReportLab PDF compilation module.
