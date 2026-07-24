// XSS prevention: escape HTML special characters in user-controlled data
function escapeHtml(str) {
    if (str === null || str === undefined) return '';
    return String(str)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

document.addEventListener("DOMContentLoaded", () => {
  const outageList = document.getElementById("outageList");
  const hero = document.getElementById("hero");
  const heroNumber = document.getElementById("heroNumber");
  const placeholder = document.getElementById("placeholder");
  const listSection = document.getElementById("listSection");
  const summary = document.getElementById("summary");
  const railFeed = document.getElementById("railFeed");
  const railCount = document.getElementById("railCount");
  const clock = document.getElementById("clock");
  const reconnectOverlay = document.getElementById("reconnectOverlay");
  const loadingSkeleton = document.getElementById("loadingSkeleton");

  const onlineAudio = new Audio("/online.mp3");
  const offlineAudio = new Audio("/offline.mp3");

  let devicesMap = new Map();
  let eventList = [];

  function fmtExactTime(iso) {
    if (!iso) return "—";
    return new Date(iso).toLocaleString('en-GB', { hour12: true }).toUpperCase();
  }

  function fmtMsg(msg) {
    if (!msg) return "";
    // XSS: escape user message before any HTML processing
    msg = escapeHtml(String(msg));
    // Remove redundant "Device <name> (<ip>)" text from standard messages
    msg = msg.replace(/^Device\s+.*?\s+\(.*?\)\s+went\s+(.*?)\.?$/i, '$1');
    msg = msg.replace(/^Monitoring for device\s+.*?\s+\(.*?\)\s+was\s+(.*?)\.?$/i, 'Monitoring was $1');
    return msg.replace(/\((Downtime|Paused for): (.*?)\)/g, '<span style="opacity:0.6">(</span><span style="opacity:0.6">$1: </span><span style="color:var(--paused); font-weight:bold">$2</span><span style="opacity:0.6">)</span>');
  }

  function renderClock() {
    clock.textContent = new Date().toLocaleTimeString('en-GB', { hour12: true }).toUpperCase();
  }
  const clockInterval = window.setInterval(renderClock, 1000);
  renderClock();

  // Clean up interval on page unload
  window.addEventListener('beforeunload', () => {
    clearInterval(clockInterval);
  });

  function updateStats() {
    fetch('/api/dashboard/stats', {credentials: 'include'})
      .then(r => r.json())
      .then(stats => {
        const elOffline = document.getElementById('wall-stat-offline');
        const elOnline = document.getElementById('wall-stat-online');
        const elPaused = document.getElementById('wall-stat-paused');
        const elTotal = document.getElementById('wall-stat-total');
        if (elOffline) elOffline.textContent = stats.offline || 0;
        if (elOnline) elOnline.textContent = stats.online;
        if (elPaused) elPaused.textContent = stats.paused || 0;
        if (elTotal) elTotal.textContent = stats.total;
      })
      .catch(e => console.error("Stats fetch error", e));
  }

  function renderState() {
    const savedScroll = outageList.scrollTop;
    loadingSkeleton.classList.add("hidden");
    outageList.innerHTML = "";
    
    let offlineArray = [];
    devicesMap.forEach((d) => {
      // Exclude paused devices (enabled === 0) from the offline list
      if (d.status === "OFFLINE" && d.enabled !== false && d.enabled !== 0) {
        offlineArray.push(d);
      }
    });

    if (!summary.innerHTML.trim()) {
      summary.innerHTML = `
        <div id="wall-stat-row" style="display: flex; justify-content: center; gap: 50px; align-items: center; margin-bottom: 10px;">
           <div id="wall-stat-offline-container" style="display: flex; flex-direction: column; align-items: flex-start;">
              <div id="wall-stat-offline" style="font-size: 2.5rem; font-weight: 700; color: var(--offline); line-height: 1;">-</div>
              <div style="font-size: 0.9rem; color: #888; margin-top: 8px; display: flex; align-items: center;">
                 <span style="display:inline-block; width:8px; height:8px; border-radius:50%; background:var(--offline); margin-right:6px; box-shadow:0 0 8px var(--offline);"></span>Offline
              </div>
           </div>
           <div style="display: flex; flex-direction: column; align-items: flex-start;">
              <div id="wall-stat-online" style="font-size: 2.5rem; font-weight: 700; color: var(--online); line-height: 1;">-</div>
              <div style="font-size: 0.9rem; color: #888; margin-top: 8px; display: flex; align-items: center;">
                 <span style="display:inline-block; width:8px; height:8px; border-radius:50%; background:var(--online); margin-right:6px; box-shadow:0 0 8px var(--online);"></span>Online
              </div>
           </div>
           <div style="display: flex; flex-direction: column; align-items: flex-start;">
              <div id="wall-stat-paused" style="font-size: 2.5rem; font-weight: 700; color: var(--paused); line-height: 1;">-</div>
              <div style="font-size: 0.9rem; color: #888; margin-top: 8px; display: flex; align-items: center;">
                 <span style="display:inline-block; width:8px; height:8px; border-radius:50%; background:var(--paused); margin-right:6px; box-shadow:0 0 8px var(--paused);"></span>Paused
              </div>
           </div>
           <div style="display: flex; flex-direction: column; align-items: flex-start;">
              <div id="wall-stat-total" style="font-size: 2.5rem; font-weight: 700; color: #fff; line-height: 1;">-</div>
              <div style="font-size: 0.9rem; color: #888; margin-top: 8px; display: flex; align-items: center; white-space: nowrap;">
                 <span style="display:inline-block; width:8px; height:8px; border-radius:50%; background:#fff; margin-right:6px; box-shadow:0 0 8px rgba(255,255,255,0.5);"></span>Total
              </div>
           </div>
        </div>
      `;
    }

    if (offlineArray.length === 0) {
      hero.classList.add("hidden");
      listSection.classList.add("hidden");
      placeholder.classList.remove("hidden");
      
      const offlineStat = document.getElementById('wall-stat-offline-container');
      if (offlineStat) offlineStat.style.display = 'flex';
      
      return;
    }

    hero.classList.remove("hidden");
    listSection.classList.remove("hidden");
    placeholder.classList.add("hidden");
    
    const offlineStat = document.getElementById('wall-stat-offline-container');
    if (offlineStat) offlineStat.style.display = 'none';
    
    heroNumber.textContent = offlineArray.length;

    const sorted = offlineArray.sort((a, b) => {
      const da = a.offline_since ? new Date(a.offline_since) : new Date(0);
      const db = b.offline_since ? new Date(b.offline_since) : new Date(0);
      return db - da; // newest first
    });

    sorted.forEach((dev) => {
      const row = document.createElement("div");
      row.className = "outage-row";
      row.dataset.id = dev.id;
      const remarkHtml = dev.remark ? ` title="${escapeHtml(dev.remark)}"` : '';
      const remarkStyle = dev.remark ? 'border-bottom: 1px dotted #888; cursor: help;' : '';

      row.innerHTML = `
        <div class="indicator offline"></div>
        <div class="name"><span${remarkHtml} style="${remarkStyle}">${escapeHtml(dev.name || "UNKNOWN")}</span></div>
        <div class="ip">${escapeHtml(dev.ip_address || "—")}</div>
        <div class="type">${escapeHtml(dev.device_type || "")}</div>
        <div class="down">${fmtExactTime(dev.offline_since)}</div>
      `;
      outageList.appendChild(row);
    });
    outageList.scrollTop = savedScroll;
  }

  function pushEvent(e) {
    eventList.unshift(e);
    // Remove items older than 24h
    const now = Date.now();
    eventList = eventList.filter(item => (now - new Date(item.timestamp).getTime()) <= 24 * 60 * 60 * 1000);
    
    // Hard cap to 50 items to prevent DOM freezing and memory leaks during aggressive flapping
    if (eventList.length > 50) {
        eventList = eventList.slice(0, 50);
    }

    railCount.textContent = eventList.length;
    renderEvents();
  }

  function renderEvents() {
    const savedScroll = railFeed.scrollTop;
    railFeed.innerHTML = "";
    eventList.forEach(e => {
      const el = document.createElement("div");
      const st = String(e.status).toLowerCase();
      const remarkHtml = e.remark ? ` title="${escapeHtml(e.remark)}"` : '';
      const remarkStyle = e.remark ? 'border-bottom: 1px dotted #888; cursor: help;' : '';

      el.className = `event ${st}`;
      el.innerHTML = `
        <div class="icon"></div>
        <div class="event-body">
          <div class="device"><span${remarkHtml} style="${remarkStyle}">${escapeHtml(e.device_name || "UNKNOWN")}</span></div>
          <div style="font-size: 0.85rem; color: rgba(255,255,255,0.5); font-family: monospace; margin-bottom: 4px;">${escapeHtml(e.ip_address || "")}</div>
          <div class="msg">${fmtMsg(e.message || e.status)}</div>
        </div>
        <div class="time">${fmtExactTime(e.timestamp)}</div>
      `;
      railFeed.appendChild(el);
    });
    railFeed.scrollTop = savedScroll;
  }

  function onStatusChange(data) {
    if (!data) return;
    
    const id = Number(data.device_id);
    const status = String(data.status || data.alert_type || "").toUpperCase();

    if (status !== "INITIALIZED") {
        pushEvent({
          device_id: id,
          device_name: data.device_name,
          ip_address: data.ip_address,
          status: status,
          message: data.message || status,
          timestamp: data.timestamp || new Date().toISOString(),
          remark: data.remark,
          _new: true,
        });
    }

    let dev = devicesMap.get(id);
    if (!dev) {
        dev = { 
            id, 
            name: data.device_name, 
            ip_address: data.ip_address, 
            device_type: data.device_type,
            remark: data.remark
        };
        devicesMap.set(id, dev);
    }
    
    if (status === "PAUSED") {
        dev.enabled = false;
    } else if (status === "RESUMED") {
        dev.enabled = true;
    } else {
        const oldStatus = dev.status;
        dev.status = status;
        
        if (status === "OFFLINE" && oldStatus !== "OFFLINE") {
            offlineAudio.play().catch(e => console.log("Audio blocked", e));
            dev.offline_since = data.timestamp;
        } else if (status === "ONLINE" && oldStatus === "OFFLINE") {
            onlineAudio.play().catch(e => console.log("Audio blocked", e));
        }
    }
    
    renderState();
    updateStats();
  }

  // Init Devices
  fetch("/api/devices", {credentials: 'include'})
    .then((r) => r.json())
    .then((data) => {
      data.forEach((d) => {
        if (d.status === "OFFLINE") {
            // Keep the actual offline_since from the backend
        }
        devicesMap.set(d.id, d);
      });
      renderState();
      updateStats();
    })
    .catch((e) => console.error("Init fetch error", e));

  // Init Alerts Feed
  fetch("/api/dashboard/events?limit=50", {credentials: 'include'})
    .then((r) => r.json())
    .then((data) => {
      if (data && data.items && data.items.length > 0) {
        eventList = data.items.map(e => ({
          device_id: e.device_id,
          device_name: e.device_name,
          ip_address: e.ip_address,
          status: e.alert_type,
          message: e.message || e.alert_type,
          timestamp: e.timestamp,
          remark: e.remark
        }));
        railCount.textContent = eventList.length;
        renderEvents();
      } else {
        railFeed.innerHTML = '<div class="placeholder small" style="margin: 40px;">No recent events</div>';
      }
    })
    .catch((e) => {
        console.error("Alerts fetch error", e);
        document.getElementById('railFeed').innerHTML = '<div class="placeholder small" style="margin: 40px;">No recent events</div>';
    });

  var initialSseConnect = true;
  var sse = window.createSSEManager({
    url: '/api/stream',
    events: {
      status_change: function(data) { onStatusChange(data); }
    },
    onConnected: function() {
      reconnectOverlay.classList.add('hidden');
      if (!initialSseConnect) {
        fetch('/api/devices', {credentials: 'include'})
          .then(function(r) { return r.json(); })
          .then(function(data) {
            data.forEach(function(d) { devicesMap.set(d.id, d); });
            renderState();
            updateStats();
          })
          .catch(function() {});
      }
      initialSseConnect = false;
      // SSE is connected — stop the polling fallback
      stopPolling();
    },
    onDenied: function(d) {
      reconnectOverlay.innerHTML =
        '<div style="font-size:3rem">\u26a0\ufe0f</div>' +
        '<div>Too many tabs open</div>' +
        '<div style="font-size:0.8rem;opacity:0.6">Close other tabs and reload this page.</div>';
      reconnectOverlay.classList.remove('hidden');
    },
    onError: function() {
      reconnectOverlay.innerHTML =
        '<div class="spinner"></div>' +
        '<div>Connection lost</div>' +
        '<div style="font-size:0.8rem;opacity:0.6">Reconnecting...</div>';
      reconnectOverlay.classList.remove('hidden');
    }
  });
  sse.connect();

  // Fallback polling when SSE not available
  var _pollTimer = null;
  function startPolling() {
    if (_pollTimer) return;
    _pollTimer = setInterval(function() {
      fetch('/api/devices')
        .then(function(r) { return r.json(); })
        .then(function(data) {
          data.forEach(function(d) { devicesMap.set(d.id, d); });
          renderState();
          updateStats();
        }).catch(function() {});

      fetch('/api/dashboard/events?limit=50', {credentials: 'include'})
        .then(function(r) { return r.json(); })
        .then(function(data) {
          if (data && data.items && data.items.length > 0) {
            eventList = data.items.map(function(e) { return {
              device_id: e.device_id,
              device_name: e.device_name,
              ip_address: e.ip_address,
              status: e.alert_type,
              message: e.message || e.alert_type,
              timestamp: e.timestamp,
              remark: e.remark
            };});
            railCount.textContent = eventList.length;
            renderEvents();
          }
        }).catch(function() {});
    }, 30000);
  }

  function stopPolling() {
    if (_pollTimer) { clearInterval(_pollTimer); _pollTimer = null; }
  }

  // Start polling as a fallback — give SSE 5 seconds to connect first
  setTimeout(startPolling, 5000);

  // Smooth auto-scrolling feature using requestAnimationFrame
  function autoScroll(el) {
    if (!el) return;
    let step = 1;
    let paused = false;
    let idleTimer;
    let ticking = false;

    el.addEventListener('mousemove', () => {
      paused = true;
      clearTimeout(idleTimer);
      idleTimer = setTimeout(() => paused = false, 2000);
    });

    el.addEventListener('mouseleave', () => {
      paused = false;
      clearTimeout(idleTimer);
    });

    function scrollTick() {
      if (document.hidden || paused || el.scrollHeight <= el.clientHeight) {
        ticking = false;
        return;
      }

      el.scrollTop += step;

      if (el.scrollTop + el.clientHeight >= el.scrollHeight) {
        step = -1;
        paused = true;
        clearTimeout(idleTimer);
        idleTimer = setTimeout(() => paused = false, 3000);
      } else if (el.scrollTop <= 0) {
        step = 1;
        paused = true;
        clearTimeout(idleTimer);
        idleTimer = setTimeout(() => paused = false, 3000);
      }

      ticking = false;
    }

    function loop() {
      if (!ticking) {
        ticking = true;
        requestAnimationFrame(scrollTick);
      }
      if (!document.hidden && !paused) {
        requestAnimationFrame(loop);
      } else {
        // Re-poll when tab becomes visible again
        setTimeout(loop, 1000);
      }
    }

    // Pause scrolling when tab is hidden
    document.addEventListener('visibilitychange', () => {
      if (document.hidden) {
        clearTimeout(idleTimer);
      }
    });

    loop();
  }

  autoScroll(outageList);
  autoScroll(railFeed);
});
