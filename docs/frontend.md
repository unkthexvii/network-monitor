# Network Monitoring System - Frontend UI Documentation

This document describes the structure, style system, and application logic of the frontend Single Page Application (SPA).

---

## 1. UI Structure (`static/index.html`)

The frontend is a fully responsive SPA built with **Bootstrap 5** and **Bootstrap Icons**, designed with a clean, dark network operations center (NOC) aesthetic.

### Navigation Sidebar
- Located on the left side of the screen.
- Allows navigation between major modules: **Dashboard**, **Topology**, **Devices**, **Alerts**, and **Reports / Settings**.
- Displays dynamic status summaries (e.g., Total online/offline counts).
- Contains the global **Audio Notification Toggle** to enable or disable audio sirens on status change.

### Main Content Panels
The page transitions dynamically using tab panes:
1. **Dashboard:** Displays quick-KPI widgets (Online, Offline, Avg Latency, Packet Loss), dynamic list of recent alerts, and real-time Chart.js charts showing network-wide latency trends.
2. **Topology:** Visualizes devices as interconnected SVG elements. Connection lines indicate latency (green/orange/red), and node sizes adjust depending on device classification. Tooltips display device remarks.
3. **Devices (Device Management):** A searchable table of all monitored endpoints. Allows bulk deletion, individual device editing, and toggling of monitoring state.
4. **Alerts (Alert History):** A filterable, paginated audit log of every connection failure and recovery event.
5. **Reports & Settings:** Configuration panel for scheduling PDF reports, customizing polling frequencies, and downloading on-demand performance summaries.

### Modals & Panels
- **Add Device Modal:** Input fields for Name, IP, Type (Switch, Router, Controller, AP, Server, etc.), Check Interval, Remarks, and detailed SNMP version settings (v2c/v3 with community strings/auth protocols/encryption keys).
- **Device Details Slide-out Offcanvas:** Triggered by clicking any device. It reveals:
  - Latency and packet loss trend graphs for selected time periods (1h, 24h, 7d).
  - Detailed SNMP System Info: System Name, Uptime, Description.
  - SNMP Asset Info: Dynamic client count indicators for WLC controllers, and hardware serial numbers.
  - Device configuration settings update form.

---

## 2. Design System (`static/style.css`)

The UI enforces a modern, dark, glassmorphic dashboard theme.

### Key CSS Variables
- `--bg-main`: Deep charcoal-black (`#0c0c0e`) to minimize eye strain.
- `--bg-card`: Dark slate-grey (`#121216`) with subtle borders (`#222`).
- `--text-primary`: Pure white (`#ffffff`) for readability.
- `--text-secondary`: Muted grey (`#8e8e93`) for labels.

### Visual Effects
- **Borders:** Thin `#222` borders replace Bootstrap's default light lines.
- **Glassmorphism:** Navigation menus, details panels, and modals use translucent dark backdrops with blur filters (`backdrop-filter: blur(10px)`).
- **Inputs:** Dark transparent forms with crisp, thin borders that glow subtle blue/violet on focus.
- **Tooltips:** Hover-activated tooltips showing remarks on device names and topology nodes.

### Animation Keyframes
- **Indicator Lights:** Pulse animations for network status dots.
  - `ind-online`: Pulsating green dot.
  - `ind-offline`: Pulsating warning red dot.
  - `ind-attention`: Steady yellow dot indicating paused or warning state.

---

## 3. Application Logic (`static/app.js`)

`app.js` is the heart of the SPA. It handles local state, parses streaming SSE telemetry, and renders charts.

### Client-Side State
```javascript
window.currentOpenDevice = null;       // Tracks currently inspected device in details panel
window.audioNotificationsEnabled = false; // Muted by default until user interacts/toggles
window.pageUpdaters = {};             // Module update callback registry
```

### Module Breakdown

#### 1. Page Router (`window.showPage`)
- Intercepts clicks on the sidebar, adds the `.active` class to selected tabs, and hides all other panels.
- Triggers the appropriate page updater function registered under `window.pageUpdaters`.

#### 2. Real-Time Stream Engine (`initSse`)
- Initiates a `EventSource` connection to `/api/stream`.
- Listens for events:
  - **`status_change`:** Fired when a device transitions (e.g., Online ↔ Offline). Triggers visual alert banners, updates status widgets, adds entries to the alert log, and invokes the sound engine.
  - **`discover_complete`:** Fired when a LAN subnet sweep completes. Populates the discovery results table with found active IPs.
  - **`discover_error`:** Fired when a LAN sweep fails or is skipped. Displays the error reason in the discovery modal.
  - **`device_cache_reload`:** Fired when devices are added, edited, or deleted, requesting the frontend to refresh cached device states.

#### 3. Sound Alarm Engine
- Browser security policies forbid playing audio before a user interacts with the page.
- The sound engine remains locked until the user clicks the "Audio Siren Toggle" or interacts with the UI.
- Once unlocked, if a device status transitions:
  - Transition to `OFFLINE` plays `static/offline.mp3`.
  - Recovery to `ONLINE` plays `static/online.mp3`.

#### 4. Dashboard Charts (Chart.js)
- Renders a multi-dataset line chart displaying the running latency of the top monitored network endpoints.
- Auto-updates dynamically as new telemetry is received via the SSE connection.

#### 5. Device Management CRUD
- Handles forms for creating, editing, and deleting devices via asynchronous fetch POST/PUT/DELETE requests.
- Implements checkboxes for bulk actions (e.g., deleting multiple devices at once).
- Handles conditional SNMP configurations: shows or hides SNMP community strings/v3 security variables depending on whether SNMP version `v2c` or `v3` is selected.
- Renders the details Offcanvas panel, which dynamically displays WLC client counters and device serial numbers under the **SNMP Asset Info** sub-header if the backend returns those SNMP metrics.

#### 6. Topology Visualization
- Renders an interactive SVG canvas.
- Renders devices as circular nodes, colored by their current status.
- Renders connection lines between nodes representing physical/logical linking cables. Line color shows latency strength (green for low latency, red for heavy latency).
- Renders tooltips displaying device remarks on hover.

### Wall Display (`static/wall.html` / `static/wall.js` / `static/wall.css`)
A separate full-screen NOC wall display page (accessible at `/wall`). Features:
- **Hero counter:** Large number showing currently offline devices.
- **Outage list:** Scrollable list of offline devices sorted by most recent downtime.
- **Event rail:** Real-time scrolling event feed (capped at 50 items, 24h window).
- **Auto-scroll:** Outage list auto-scrolls with pause-on-hover.
- **Periodic refresh:** Re-fetches status and events every 30s as SSE fallback.
- **Status bar:** Online/Offline/Paused/Total counts.
