# Network Monitoring System - Frontend UI Documentation

This document describes the structure, style system, and application logic of the frontend Single Page Application (SPA) and the NOC wall display.

---

## 1. UI Structure (`static/index.html`)

The frontend is a fully responsive SPA built with **Bootstrap 5** and **Bootstrap Icons**, designed with a clean, dark network operations center (NOC) aesthetic.

### Navigation Sidebar
- Located on the left side of the screen.
- Allows navigation between major modules: **Dashboard**, **Topology**, **Devices**, **Alerts**, and **Reports**.
- Displays dynamic status summaries (total online/offline counts).
- Shows login/logout button based on authentication state.

### Main Content Panels
The page transitions dynamically using tab panes:

1. **Dashboard:** Displays KPI widgets (Online, Offline, Paused, Total counts), a paginated offline device list, and a paginated recent events feed (24h window).
2. **Topology:** Interactive network topology map using **vis-network** (loaded from CDN). Multi-tab support for locations, search/filter, save layout, toggle physics, and fullscreen. Nodes colored by status, edges colored by latency. Add/delete topology locations and add nodes from device list.
3. **Devices (Device Management):** A searchable, paginated table of all monitored endpoints. Allows bulk deletion, individual device editing, monitoring toggle, and LAN subnet discovery.
4. **Alerts (Alert History):** A filterable, paginated audit log of every connection failure and recovery event. Supports time/status/device filtering with grouped-by-device or per-device views.
5. **Reports (Performance Reports):** Per-device performance report view with uptime percentage, latency heatmap, incident timeline, and PDF export/print. No scheduling or settings UI.

### Modals & Panels
- **Login Modal:** Username/password form shown when session expires, uses `POST /api/auth/login` with session cookie.
- **Change Password Modal:** Current/new/confirm password fields with 12-char minimum validation.
- **Add Device Modal:** Input fields for Name, IP, Type (30+ types in 6 categories: Core Network, Servers & Databases, Infrastructure & Storage, Endpoints & Peripherals, Security/IoT/Other), Check Interval, Site, Location, Rack, Vendor, Model, Remarks, and detailed SNMP version settings (v2c/v3 with community strings/auth protocols/encryption keys).
- **Device Details Slide-out Offcanvas:** Triggered by clicking any device. It reveals:
  - Latency and packet loss trend graphs for selected time periods (1h, 24h, 7d).
  - Detailed SNMP System Info: System Name, Uptime, Description, Contact, Location.
  - SNMP Asset Info: WLC client count, AP count, hardware serial number, model name, chassis name, custom data fields.
  - Device metadata: Site, Location, Rack, Vendor, Model, check interval, thresholds.

---

## 2. Design System (`static/style.css`)

The UI enforces a dark, modern NOC dashboard theme.

### Key CSS Variables
- `--bg-base`: Deep charcoal-black (`#0a0a0a`) for page background.
- `--bg-card`: Dark slate-grey (`#161616`) for cards and panels.
- `--text-primary`: Near-white (`#f5f5f5`) for readability.
- `--text-secondary`: Muted grey (`#8c8c8c`) for labels.

### Visual Effects
- **Borders:** Thin `#1f1f1f` / `#2a2a2a` borders replace Bootstrap's default light lines.
- **Sidebar:** Solid `#121212` background, no backdrop blur.
- **Modals & Offcanvas:** Solid `#1e1e1e` backgrounds.
- **Inputs:** Dark transparent forms with crisp, thin borders that glow subtle blue/violet on focus.
- **Tooltips:** Hover-activated tooltips showing remarks on device names and topology nodes.

### Status Indicators
- **`ind-online`:** Green dot with green box-shadow — static, no animation.
- **`ind-offline`:** Red dot with red box-shadow — static, no animation.
- **`ind-attention`:** Yellow dot — steady for paused or warning state.
- **`ind-unknown`:** Grey dot — device status not yet determined.

---

## 3. Shared Module (`static/sse.js`)

A standalone SSE connection manager used by both `app.js` and `wall.js`. Features:
- **Exponential-backoff reconnect** on connection loss.
- **Connection-denial handling:** Shows a "too many tabs" overlay when the backend rejects a new SSE client.
- **Factory API:** `createSSEManager({ url, events, onConnected, onDenied, onError })` returns a manager with `.connect()` and `.close()`.

---

## 4. Application Logic (`static/app.js`)

`app.js` is the heart of the SPA. It handles page routing, SSE streaming, auth state, and CRUD operations.

### Client-Side State
```javascript
window.pageUpdaters = {};          // Module update callback registry
window.isAuthenticated = false;    // Tracks login state
window.isReadonly = false;         // Tracks global readonly mode
window.sse = null;                 // SSE manager instance
```

### Auth & Read-Only System
- On every page switch, `refreshReadonly()` fetches `/api/readonly` to check auth and readonly status.
- `authFetch()` wrapper: adds `Authorization: Bearer <token>` header for mutating requests; blocks POST/PUT/DELETE in readonly mode with an alert.
- `updateAuthUI()` shows/hides UI elements (login button, change password, admin-only actions) based on auth state.
- Login modal and change-password modal are conditionally rendered.

### Module Breakdown

#### 1. Page Router (`window.showPage`)
- Intercepts clicks on the sidebar, adds the `.active` class to selected tabs, and hides all other panels.
- Triggers the appropriate page updater function registered under `window.pageUpdaters`.

#### 2. Real-Time Stream Engine (`initSse`)
- Uses `createSSEManager` from `sse.js` to connect to `/api/stream`.
- Listens for events:
  - **`status_change`:** Fired when a device transitions (e.g., Online ↔ Offline). Triggers visual alert banners, updates status widgets, adds entries to the alert log, and invokes the sound engine.
  - **`discover_complete`:** Fired when a LAN subnet sweep completes. Populates the discovery results table with found active IPs.
  - **`discover_error`:** Fired when a LAN sweep fails or is skipped. Displays the error reason in the discovery modal.

#### 3. Sound Alarm Engine
- Browser security policies forbid playing audio before a user interacts with the page.
- Sound is unlocked on first user interaction (click/tap).
- Once unlocked, if a device status transitions:
  - Transition to `OFFLINE` plays `static/offline.mp3`.
  - Recovery to `ONLINE` plays `static/online.mp3`.
- Audio plays unconditionally — no toggle to disable.

#### 4. Dashboard KPI Updaters
- Fetches `/api/dashboard/stats` for four counts: Online, Offline, Paused, Total.
- Fetches `/api/dashboard/events?limit=50` for the recent events feed.
- Fetches `/api/devices/paginated?status=OFFLINE` for the offline device list.
- No Chart.js or latency chart rendering on the dashboard.

#### 5. Device Management CRUD
- Handles forms for creating, editing, and deleting devices via `authFetch` POST/PUT/DELETE requests.
- Implements checkboxes for bulk actions (e.g., deleting multiple devices at once).
- Handles conditional SNMP configurations: shows or hides SNMP community strings/v3 security variables depending on whether SNMP version `v2c` or `v3` is selected.
- Renders the details Offcanvas panel, which dynamically displays WLC client counters and device serial numbers under the **SNMP Asset Info** sub-header if the backend returns those SNMP metrics.
- LAN discovery: fetches `/api/devices/subnets` for available subnets, then POSTs to `/api/devices/discover` to start a sweep. Results populate a table with checkboxes for bulk import.

#### 6. Topology Visualization
- Uses **vis-network** library (loaded from CDN).
- Renders devices as circular nodes, colored by their current status.
- Renders connection lines between nodes representing physical/logical links. Line color shows latency strength (green for low latency, red for high latency).
- Multi-tab support for organizing topology into locations with search and dropdown.
- Action buttons: Save Layout, Fit Screen, Toggle Physics, Fullscreen.
- Node manipulation (add/edit/delete) enabled only when not in readonly mode.
- Tooltips displaying device remarks on hover.

---

## 5. Wall Display (`static/wall.html` / `static/wall.js` / `static/wall.css`)

A separate full-screen NOC wall display page (accessible at `/wall`). Features:
- **Hero counter:** Large number showing currently offline devices.
- **Outage list:** Scrollable list of offline devices sorted by most recent downtime.
- **Event rail:** Real-time scrolling event feed (capped at 50 items, 24h window).
- **Auto-scroll:** Outage list auto-scrolls with pause-on-hover.
- **Periodic refresh:** Fetches device list on page load via `GET /api/devices`. Starts fallback polling after 5 seconds (every 30s) that **stops once SSE connects**.
- **SSE integration:** Uses `createSSEManager` from `sse.js` for real-time status changes and reconnects with exponential backoff.
- **Status bar:** Online/Offline/Paused/Total counts.
- **Audio alerts:** Plays offline/online sounds on status transitions.
