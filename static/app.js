/**
 * NetMon SPA - Single Page Application
 * Hash-based routing with persistent SSE connection.
 */
(function() {
    'use strict';

    // Initialize global state
    window.discoveredDevices = {};

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

    window.toggleSnmpFields = function() {
        const v = document.getElementById('newDeviceSnmpVersion').value;
        document.getElementById('snmpV2Fields').style.display = (v === 'v2c') ? 'block' : 'none';
        document.getElementById('snmpV3Fields').style.display = (v === 'v3') ? 'block' : 'none';
    };

    window.toggleSnmpEditFields = function() {
        const v = document.getElementById('editSnmpVersion').value;
        document.getElementById('editSnmpV2Fields').style.display = (v === 'v2c') ? 'block' : 'none';
        document.getElementById('editSnmpV3Fields').style.display = (v === 'v3') ? 'block' : 'none';
    };

    let _confirmHandler = null;
    window.showConfirmModal = function(title, message, btnText, btnClass, onConfirm) {
        document.getElementById('confirmModalTitle').innerText = title;
        document.getElementById('confirmModalBody').innerText = message;
        const btn = document.getElementById('confirmModalBtn');
        btn.innerText = btnText;
        btn.className = `btn ${btnClass}`;
        if (_confirmHandler) btn.removeEventListener('click', _confirmHandler);
        _confirmHandler = () => { onConfirm(); modal.hide(); };
        btn.addEventListener('click', _confirmHandler);
        const modal = bootstrap.Modal.getOrCreateInstance(document.getElementById('confirmModal'));
        modal.show();
    };

    // Debounce utility for search inputs — prevents DOM thrash on every keystroke
    function debounce(fn, ms) {
        let timer;
        return function(...args) {
            clearTimeout(timer);
            timer = setTimeout(() => fn.apply(this, args), ms);
        };
    }

    window.renderTimelineEvent = function(evt) {
        const getEventIcon = (type) => {
            if (type === 'OFFLINE') return 'bi-x-circle-fill text-danger';
            if (type === 'PAUSED') return 'bi-pause-circle-fill text-warning';
            if (type === 'RESUMED') return 'bi-play-circle-fill text-success';
            return 'bi-check-circle-fill text-success';
        };
        const getAlertColor = (type) => type === 'OFFLINE' ? 'danger' : (type === 'PAUSED' ? 'warning' : 'success');
        
        const sc = `text-${getAlertColor(evt.alert_type)}`;
        const iconClass = getEventIcon(evt.alert_type);
        const ts = new Date(evt.timestamp).toLocaleString('en-GB', { hour12: true }).toUpperCase();
        
        let msgHtml = escapeHtml(evt.message || '').replace(/\((Downtime|Paused for): (.*?)\)/g, '<span class="text-secondary">(</span><span class="text-secondary">$1: </span><span class="text-warning fw-bold">$2</span><span class="text-secondary">)</span>');
        
        const tsHtml = `<span class="badge" style="background-color: rgba(0,0,0,0.3); color: #aaa; border: 1px solid #333; font-family: monospace; font-weight: normal; padding: 0.35em 0.5em; font-size: 0.72rem;">${ts}</span>`;
        
        return `<div class="timeline-item" style="padding-bottom: 0.5rem;"><i class="bi ${iconClass}" style="position: absolute; left: -6px; top: 0px; font-size: 1rem; z-index: 2; background-color: #1a1a1a; border-radius: 50%;"></i><div class="d-flex flex-column"><div class="d-flex align-items-center mb-1"><span class="fw-bold ${sc} me-3" style="font-size: 0.85rem;">${escapeHtml(evt.alert_type)}</span>${tsHtml}</div><div class="text-light" style="font-size: 0.85rem; opacity: 0.9;">${msgHtml}</div></div></div>`;
    };

    // =========================================================
    // Router
    // =========================================================
    const PAGES = ['dashboard', 'topology', 'devices', 'alerts', 'reports'];
    let currentPage = null;

    function navigateTo(page, opts) {
        if (!PAGES.includes(page)) page = 'dashboard';
        if (page === currentPage) return;

        // Hide all page sections
        document.querySelectorAll('.page-section').forEach(s => {
            s.classList.add('d-none');
            s.classList.remove('active-page');
        });

        // Show target page
        const target = document.getElementById('page-' + page);
        if (target) {
            target.classList.remove('d-none');
            target.classList.add('active-page');
        }

        // Update sidebar active state
        document.querySelectorAll('.nav-icon').forEach(a => a.classList.remove('active'));
        const navEl = document.getElementById('nav-' + page);
        if (navEl) navEl.classList.add('active');

        // Update document title
        const titles = {
            dashboard: 'Dashboard',
            topology: 'Network Topology',
            devices: 'Device Management',
            alerts: 'Alert History',
            reports: 'Performance Reports'
        };
        document.title = 'Network Monitoring - ' + (titles[page] || 'Dashboard');

        currentPage = page;

        // Sync the browser URL bar with the current SPA page
        // Skip on popstate (browser already updated the URL)
        if (!opts || !opts.skipPush) {
            window.history.pushState({}, '', '/' + page);
        }

        // Fetch data for the new page
        if (pageUpdaters[page]) pageUpdaters[page]();
        
        // Re-apply readonly/unauth UI state after page renders new DOM elements
        updateAuthUI();
    }

    // Expose globally so inline onclick attributes in index.html can call it
    window.navigateTo = navigateTo;

    function handleRoute() {
        const path = window.location.pathname.replace(/^\/+|\/+$/g, '') || 'dashboard';
        navigateTo(path, { skipPush: true });
    }

    // Intercept navigation for SPA
    document.addEventListener('click', e => {
        const a = e.target.closest('a');
        if (a) {
            const href = a.getAttribute('href');
            // Intercept internal routes
            if (href && href.startsWith('/') && !href.startsWith('/api/') && href !== '/wall' && a.getAttribute('target') !== '_blank') {
                e.preventDefault();
                const page = href.replace(/^\//, '');
                navigateTo(page);
            }
        }
    });

    // =========================================================
    // Sound Alerts (VMping style)
    // =========================================================
    const audioOnline = new Audio('/online.mp3');
    const audioOffline = new Audio('/offline.mp3');

    function playStatusSound(status) {
        if (status === 'ONLINE' || status === 'RESUMED') {
            audioOnline.currentTime = 0;
            audioOnline.play().catch(e => console.log("Audio play blocked by browser:", e));
        } else if (status === 'OFFLINE') {
            audioOffline.currentTime = 0;
            audioOffline.play().catch(e => console.log("Audio play blocked by browser:", e));
        }
    }

    // =========================================================
    // SSE — managed via shared sse.js
    // =========================================================
    function showSseBanner(html, persistent) {
        var el = document.getElementById('sseBanner');
        if (!el) {
            el = document.createElement('div');
            el.id = 'sseBanner';
            el.style.cssText = 'position:fixed;top:0;left:0;right:0;z-index:99999;background:#b71c1c;color:#fff;text-align:center;padding:8px;font-size:0.85rem;display:none;';
            document.body.prepend(el);
        }
        el.innerHTML = html;
        el.style.display = 'block';
        if (!persistent) setTimeout(function() { el.style.display = 'none'; }, 8000);
    }

    var sse = window.createSSEManager({
        url: '/api/stream',
        events: {
            status_change: function(data) {
                triggerRefresh();
                if (data.status) playStatusSound(data.status);
            },
            discover_complete: function(data) { handleDiscoverComplete(data); },
            discover_error: function(data) {
                var state2 = document.getElementById('discoverState2');
                var state3 = document.getElementById('discoverState3');
                if (state2) state2.classList.add('d-none');
                if (state3) {
                    state3.classList.remove('d-none');
                    var cnt = document.getElementById('discoveredCount');
                    if (cnt) cnt.innerText = '0';
                    var list = document.getElementById('discoverResultsList');
                    if (list) list.innerHTML = '<tr><td colspan="4" class="text-danger text-center">Discovery failed: ' + escapeHtml(data.reason || 'Unknown error') + '</td></tr>';
                }
            }
        },
        onConnected: function() {
            var el = document.getElementById('sseBanner');
            if (el) el.style.display = 'none';
        },
        onDenied: function(d) {
            showSseBanner('Too many tabs open (' + escapeHtml(d.current) + '/' + escapeHtml(d.limit) + '). Close other tabs and reload.', true);
        },
        onError: function() {
            showSseBanner('Live updates paused \u2014 reconnecting...', false);
        }
    });
    sse.connect();

    // =========================================================
    // Auth & Read-Only Mode
    // =========================================================
    window.isAuthenticated = false;

    function isWriteBlocked() {
        return window.isReadonly || !window.isAuthenticated;
    }

    function authFetch(url, options) {
        options = options || {};
        options.credentials = 'include';
        // Guard mutating requests when readonly is active
        if (url.startsWith('/api/') && options.method && options.method !== 'GET') {
            const blocked = isWriteBlocked();
            if (blocked && url !== '/api/auth/login' && url !== '/api/auth/logout' && url !== '/api/admin/readonly' && !url.startsWith('/api/auth/')) {
                showReadonlyToast();
                return Promise.reject(new Error('Read-only mode active'));
            }
        }
        return fetch(url, options);
    }

    function showReadonlyToast() {
        showErrorToast('Read-only mode active — changes are disabled', 3000);
    }

    function showErrorToast(message, duration) {
        duration = duration || 4000;
        const existing = document.getElementById('errorToast');
        if (existing) existing.remove();
        const toast = document.createElement('div');
        toast.id = 'errorToast';
        toast.style.cssText = 'position:fixed; bottom:20px; left:50%; transform:translateX(-50%); z-index:99999; background:#1e1e1e; color:#ff6b6b; border:1px solid #ff6b6b44; border-radius:8px; padding:12px 24px; font-size:0.9rem; box-shadow:0 4px 20px rgba(0,0,0,0.5); transition:opacity 0.3s;';
        toast.innerHTML = '<i class="bi bi-exclamation-triangle me-2"></i> ' + escapeHtml(message);
        document.body.appendChild(toast);
        setTimeout(() => { toast.style.opacity = '0'; setTimeout(() => toast.remove(), 300); }, duration);
    }

    function refreshReadonly() {
        return fetch('/api/readonly', {credentials: 'include'}).then(r => r.json()).then(data => {
            window.isReadonly = data.readonly;
            window.isAuthenticated = data.authenticated;
            updateAuthUI();
        });
    }

    window.toggleReadonly = function() {
        const newVal = !window.isReadonly;
        authFetch('/api/admin/readonly', {
            method: 'POST',
            body: JSON.stringify({readonly: newVal})
        }).then(r => {
            if (!r.ok) throw new Error('Failed to toggle read-only mode');
            return r.json();
        }).then(data => {
            window.isReadonly = data.readonly;
            updateAuthUI();
            // Refresh the page list to reflect new state
            triggerRefresh(false);
        }).catch(e => showErrorToast(e.message));
    };

    window.doLogin = function() {
        const pw = document.getElementById('loginPassword').value;
        const btn = document.getElementById('loginBtn');
        const err = document.getElementById('loginError');
        btn.disabled = true;
        btn.innerText = 'Logging in...';
        fetch('/api/auth/login', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            credentials: 'include',
            body: JSON.stringify({password: pw})
        }).then(r => {
            if (!r.ok) { err.classList.remove('d-none'); btn.disabled = false; btn.innerText = 'Login'; return null; }
            return r.json();
        }).then(data => {
            if (data && data.ok) {
                bootstrap.Modal.getInstance(document.getElementById('loginModal')).hide();
                document.getElementById('loginPassword').value = '';
                err.classList.add('d-none');
                refreshReadonly().then(() => triggerRefresh(false));
            }
        }).catch(() => { btn.disabled = false; btn.innerText = 'Login'; });
    };

    window.doLogout = function() {
        authFetch('/api/auth/logout', {method: 'POST'}).catch(() => {});
        refreshReadonly().then(() => triggerRefresh(false)).catch(() => {});
    };

    window.doChangePassword = function() {
        const currentPw = document.getElementById('changePwCurrent');
        const newPw = document.getElementById('changePwNew');
        const confirmPw = document.getElementById('changePwConfirm');
        const btn = document.getElementById('changePwBtn');
        const err = document.getElementById('changePwError');
        const ok = document.getElementById('changePwSuccess');
        
        ok.classList.add('d-none');
        err.classList.add('d-none');

        if (!currentPw.value || !newPw.value || !confirmPw.value) {
            err.textContent = 'All fields are required';
            err.classList.remove('d-none'); return;
        }
        if (newPw.value.length < 4) {
            err.textContent = 'New password must be at least 4 characters';
            err.classList.remove('d-none'); return;
        }
        if (newPw.value !== confirmPw.value) {
            err.textContent = 'Passwords do not match';
            err.classList.remove('d-none'); return;
        }

        btn.disabled = true;
        btn.innerText = 'Changing...';
        authFetch('/api/auth/change-password', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({current_password: currentPw.value, new_password: newPw.value})
        }).then(r => {
            if (r.ok) return r.json();
            return r.json().then(d => { throw new Error(d.detail || 'Failed to change password'); });
        }).then(() => {
            ok.classList.remove('d-none');
            currentPw.value = ''; newPw.value = ''; confirmPw.value = '';
            setTimeout(() => bootstrap.Modal.getInstance(document.getElementById('changePwModal'))?.hide(), 1500);
        }).catch(e => {
            err.textContent = e.message;
            err.classList.remove('d-none');
        }).finally(() => {
            btn.disabled = false;
            btn.innerText = 'Change Password';
        });
    };

    window.showLoginModal = function() {
        const btn = document.getElementById('loginBtn');
        const err = document.getElementById('loginError');
        const pw = document.getElementById('loginPassword');
        if (btn) { btn.disabled = false; btn.innerText = 'Login'; }
        if (err) err.classList.add('d-none');
        if (pw) pw.value = '';
        const modal = bootstrap.Modal.getOrCreateInstance(document.getElementById('loginModal'));
        modal.show();
        if (pw) pw.focus();
    };

    function updateAuthUI() {
        const isAuthed = window.isAuthenticated;
        const isReadonly = isWriteBlocked();

        function hideEl(el, hide) {
            if (!el) return;
            if (hide) el.classList.add('d-none');
            else el.classList.remove('d-none');
        }

        // --- Auth button ---
        const btnAuth = document.getElementById('btnAuth');
        if (btnAuth) {
            if (isAuthed && !window.isReadonly) {
                btnAuth.innerHTML = '<i class="bi bi-unlock-fill" style="color: #4caf50;"></i>';
                btnAuth.title = 'Logout';
                btnAuth.onclick = doLogout;
            } else if (isAuthed && window.isReadonly) {
                btnAuth.innerHTML = '<i class="bi bi-lock-fill" style="color: #ffc107;"></i>';
                btnAuth.title = 'Disable Read-Only';
                btnAuth.onclick = toggleReadonly;
            } else {
                btnAuth.innerHTML = '<i class="bi bi-lock" style="color: #888;"></i>';
                btnAuth.title = 'Login';
                btnAuth.onclick = showLoginModal;
            }
        }

        // --- Change password gear icon (visible when authenticated) ---
        const gearBtn = document.getElementById('btnChangePw');
        if (gearBtn) {
            if (isAuthed) {
                gearBtn.style.display = '';
                gearBtn.onclick = () => {
                    document.getElementById('changePwCurrent').value = '';
                    document.getElementById('changePwNew').value = '';
                    document.getElementById('changePwConfirm').value = '';
                    document.getElementById('changePwError').classList.add('d-none');
                    document.getElementById('changePwSuccess').classList.add('d-none');
                    bootstrap.Modal.getOrCreateInstance(document.getElementById('changePwModal')).show();
                };
            } else {
                gearBtn.style.display = 'none';
            }
        }

        // --- Device Management: action buttons ---
        toggleBulkDeleteBtn();
        hideEl(document.querySelector('button[data-bs-target="#addDeviceModal"]'), isReadonly);
        hideEl(document.querySelector('button[data-bs-target="#discoverLanModal"]'), isReadonly);
        hideEl(Array.from(document.querySelectorAll('button')).find(b => b.textContent.includes('Export CSV')), isReadonly);

        // --- Device list: row checkboxes and trash icons ---
        document.querySelectorAll('.row-checkbox, #device-management-list .bi-trash').forEach(el => hideEl(el, isReadonly));

        // --- Topology: ALL buttons ---
        ['btnSaveTopology','btnFitTopology','btnPhysicsToggle','btnAddTopologyTab','btnDeleteTopologyTab']
            .forEach(id => hideEl(document.getElementById(id), isReadonly));

        // --- Device detail offcanvas: buttons ---
        ['btnSaveConfig','btnToggleMonitoring'].forEach(id => hideEl(document.getElementById(id), isReadonly));

        // --- Device detail offcanvas: form fields ---
        document.querySelectorAll('#deviceDetailPanel form input, #deviceDetailPanel form select, #deviceDetailPanel form textarea')
            .forEach(f => { f.disabled = isReadonly; });

        // --- Topology manipulation ---
        window._topologyEnabled = !isReadonly;
        if (window.networkInstance) {
            window.networkInstance.setOptions({manipulation: {enabled: window._topologyEnabled}});
        }
    }

    // Assume readonly until server responds (prevents flash of editable UI)
    window.isReadonly = true;
    fetch('/api/readonly', {credentials: 'include'}).then(r => r.json()).then(data => {
        window.isReadonly = data.readonly;
        window.isAuthenticated = data.authenticated;
        updateAuthUI();
    });

    // =========================================================
    // Debounced Refresh — only updates the CURRENT page
    // =========================================================
    let _refreshTimer = null;
    function triggerRefresh(isSseUpdate = true) {
        if (_refreshTimer) clearTimeout(_refreshTimer);
        _refreshTimer = setTimeout(() => {
            const doUpdate = () => {
                if (currentPage === 'reports') return; // Reports show historical data, no auto-refresh needed
                if (currentPage && pageUpdaters[currentPage]) {
                    pageUpdaters[currentPage](isSseUpdate);
                }
            };
            if (!isSseUpdate) {
                loadAvailableDevices().then(() => {
                    doUpdate();
                    updateAuthUI();
                });
            } else {
                doUpdate();
                updateAuthUI();
            }
        }, 500);
    }

    // =========================================================
    // Page Updaters — each only fetches what it needs
    // =========================================================
    window.pageUpdaters = {};
    const pageUpdaters = window.pageUpdaters;

    window.paginationState = { devices: 1, alerts: 1, reports: 1 };
    const PER_PAGE = 10;

    function renderPagination(containerId, totalItems, pageId, updaterKey) {
        updaterKey = updaterKey || pageId;
        const container = document.getElementById(containerId);
        if (!container) return;
        const totalPages = Math.ceil(totalItems / PER_PAGE) || 1;
        let page = window.paginationState[pageId] || 1;
        if (page > totalPages) { page = totalPages; window.paginationState[pageId] = page; }
        
        let html = `<div class="d-flex justify-content-end align-items-center gap-2">`;
        
        // Prev button
        const prevDis = page === 1 ? 'disabled style="opacity:0.5; cursor:not-allowed;"' : `data-action="paginate" data-page="${page-1}" data-page-id="${pageId}" data-updater="${updaterKey}"`;
        html += `<button class="btn btn-sm shadow-none btn-pagination" style="background: rgba(255,255,255,0.05); border: 1px solid #333; color: #aaa; width: 32px; height: 32px; display: flex; align-items: center; justify-content: center; transition: all 0.2s;" ${prevDis}><i class="bi bi-chevron-left" style="font-size:0.7rem;"></i></button>`;
        
        // Page numbers
        let pages = [];
        if (totalPages <= 7) {
            for (let i = 1; i <= totalPages; i++) pages.push(i);
        } else {
            if (page <= 4) {
                pages = [1, 2, 3, 4, 5, '...', totalPages];
            } else if (page >= totalPages - 3) {
                pages = [1, '...', totalPages - 4, totalPages - 3, totalPages - 2, totalPages - 1, totalPages];
            } else {
                pages = [1, '...', page - 1, page, page + 1, '...', totalPages];
            }
        }
        
        pages.forEach(i => {
            if (i === '...') {
                html += `<div class="d-flex align-items-center justify-content-center text-secondary" style="width: 32px; height: 32px;">...</div>`;
            } else if (i === page) {
                html += `<button class="btn btn-sm shadow-none fw-bold" style="background: rgba(255,255,255,0.15); border: 1px solid #555; color: #fff; width: 32px; height: 32px; display: flex; align-items: center; justify-content: center;">${i}</button>`;
            } else {
                html += `<button class="btn btn-sm shadow-none btn-pagination" style="background: transparent; border: 1px solid transparent; color: #888; width: 32px; height: 32px; display: flex; align-items: center; justify-content: center; transition: all 0.2s;" data-action="paginate" data-page="${i}" data-page-id="${pageId}" data-updater="${updaterKey}">${i}</button>`;
            }
        });
        
        // Next button
        const nextDis = page === totalPages ? 'disabled style="opacity:0.5; cursor:not-allowed;"' : `data-action="paginate" data-page="${page+1}" data-page-id="${pageId}" data-updater="${updaterKey}"`;
        html += `<button class="btn btn-sm shadow-none btn-pagination" style="background: rgba(255,255,255,0.05); border: 1px solid #333; color: #aaa; width: 32px; height: 32px; display: flex; align-items: center; justify-content: center; transition: all 0.2s;" ${nextDis}><i class="bi bi-chevron-right" style="font-size:0.7rem;"></i></button>`;
        
        html += `</div>`;
        container.innerHTML = html;
    }

    function fetchPaginated(url, pageId, containerId, updaterKey, renderCallback, noResultsCallback) {
        const page = window.paginationState[pageId] || 1;
        const sep = url.includes('?') ? '&' : '?';
        const finalUrl = `${url}${sep}page=${page}&limit=${PER_PAGE}`;
        
        fetch(finalUrl, {credentials: 'include'})
            .then(r => {
                if (!r.ok) throw new Error(`HTTP ${r.status}`);
                return r.json();
            })
            .then(data => {
                if (!data || typeof data.total === 'undefined') return;
                if (data.total === 0) {
                    noResultsCallback();
                    renderPagination(containerId, 0, pageId, updaterKey);
                } else {
                    renderCallback(data.items, data.total);
                    renderPagination(containerId, data.total, pageId, updaterKey);
                }
            })
            .catch(e => {
                console.error('fetchPaginated error:', e);
                noResultsCallback();
                renderPagination(containerId, 0, pageId, updaterKey);
            });
    }

    // --- DASHBOARD ---
    pageUpdaters.dashboard = function() {
        fetch('/api/dashboard/stats', {credentials: 'include'}).then(r => r.json()).then(stats => {
            document.getElementById('stat-online').textContent = stats.online;
            document.getElementById('stat-offline').textContent = stats.offline;
            document.getElementById('stat-paused').textContent = stats.paused || 0;
            document.getElementById('stat-total').textContent = stats.total;
        }).catch(() => {});

        fetchPaginated('/api/dashboard/events', 'dashboardEvents', 'dashboardEventsPagination', 'dashboard', (items) => {
            const feed = document.getElementById('events-feed');
            if (!feed) return;
            const getEventIcon = (type) => {
                if (type === 'OFFLINE') return 'bi-x-circle text-danger';
                if (type === 'PAUSED') return 'bi-pause-circle text-warning';
                if (type === 'RESUMED') return 'bi-play-circle text-success';
                return 'bi-check-circle text-success';
            };
            const getEventTextColor = (type) => {
                if (type === 'OFFLINE') return 'danger';
                if (type === 'PAUSED') return 'warning';
                return 'success';
            };
            feed.innerHTML = items.map(evt => {
                const iconClass = getEventIcon(evt.alert_type);
                const textColor = getEventTextColor(evt.alert_type);
                const timeStr = new Date(evt.timestamp).toLocaleTimeString('en-GB', { hour12: true }).toUpperCase();
                const remarkHtml = evt.remark ? ` title="${escapeHtml(evt.remark)}"` : '';
                const remarkStyle = evt.remark ? 'border-bottom: 1px dotted #888; cursor: help;' : '';
                return `
                    <li class="feed-item">
                        <div class="feed-icon"><i class="bi ${iconClass}"></i></div>
                        <div class="feed-details">
                            <div class="feed-device"><span${remarkHtml} style="${remarkStyle}">${escapeHtml(evt.device_name)}</span> <span class="text-secondary fw-normal ms-2" style="font-size:0.8rem;">${escapeHtml(evt.ip_address)}</span></div>
                            <div class="feed-time">${timeStr}</div>
                        </div>
                        <div class="feed-status text-${textColor}">${escapeHtml(evt.alert_type)}</div>
                    </li>`;
            }).join('');
        }, () => {
            const feed = document.getElementById('events-feed');
            if (feed) feed.innerHTML = '<li class="feed-item text-secondary">No recent events.</li>';
        });

        fetchPaginated('/api/devices/paginated?status=OFFLINE', 'dashboard', 'dashboardPagination', 'dashboard', (items) => {
            const list = document.getElementById('offline-devices-list');
            if (!list) return;
            list.innerHTML = items.map(dev => {
                const remarkHtml = dev.remark ? ` title="${escapeHtml(dev.remark)}"` : '';
                const remarkStyle = dev.remark ? 'border-bottom: 1px dotted #888; cursor: help;' : '';
                return `<tr><td class="text-white fw-medium"><span${remarkHtml} style="${remarkStyle}">${escapeHtml(dev.name)}</span></td><td class="text-secondary" style="font-family: monospace;">${escapeHtml(dev.ip_address)}</td></tr>`;
            }).join('');
        }, () => {
            const list = document.getElementById('offline-devices-list');
            if (list) list.innerHTML = '<tr><td colspan="2" class="text-center text-secondary py-4" style="border: none;">No offline devices.</td></tr>';
        });
    };

        // --- TOPOLOGY ---
    window.topologyTabs = [];
    window.currentTopologyTabId = null;
    window.topologyNodesMap = {}; // tab_id -> nodes
    window.topologyEdgesMap = {}; // tab_id -> edges
    window.allDevices = [];

    function loadAvailableDevices() {
        return fetch('/api/devices/names', {credentials: 'include'}).then(r => r.json()).then(devices => {
            // Quick comparison: same length and same last device id avoids full JSON.stringify
            const prev = window.allDevices;
            if (prev && prev.length === devices.length &&
                prev.length > 0 && devices.length > 0 &&
                prev[prev.length - 1].id === devices[devices.length - 1].id) {
                return;
            }
            window.allDevices = devices;
            ['devicesPageSearchInput', 'alertsPageSearchInput', 'reportDeviceSearchInput'].forEach(id => {
                const el = document.getElementById(id);
                if (el) el.dispatchEvent(new Event('input'));
            });
        });
    }

    function renderTopologyTabsList() {
        renderComboList('topologyTabsList', 'topologySiteSearchInput', window.topologyTabs, ['name'], null, (id, name) => {
            const input = document.getElementById('topologySiteSearchInput');
            if (input) input.value = name;
            switchTopologyTab(id);
        });
    }
    
    // Add input listener for filtering the site list
    document.addEventListener('input', e => {
        if (e.target && e.target.id === 'topologySiteSearchInput') {
            renderTopologyTabsList();
        }
    });

    window.switchTopologyTab = function(tabId) {
        window.currentTopologyTabId = tabId;
        const tab = window.topologyTabs.find(t => t.id === tabId);
        if (tab) {
            const input = document.getElementById('topologySiteSearchInput');
            if (input) input.value = tab.name;
        }
        renderTopologyTabsList();
        
        const container = document.getElementById('topology-network');
        if (!container) return;
        
        const tabNodes = window.topologyNodesMap[tabId] || [];
        const tabEdges = window.topologyEdgesMap[tabId] || [];
        
        const hasPositions = tabNodes.some(n => n.x !== undefined && n.y !== undefined);
        const nodes = new vis.DataSet(tabNodes);
        const edges = new vis.DataSet(tabEdges);
        
        const locales = {
            en: {
                edit: 'Edit', del: 'Delete selected', back: 'Back',
                addNode: 'Add Device', addEdge: 'Add Connection',
                editNode: 'Edit Device', editEdge: 'Edit Connection',
                addDescription: 'Click in an empty space to place a new device.',
                edgeDescription: 'Click on a device and drag the connection to another device.',
                editEdgeDescription: 'Click on the control points and drag them to a device.'
            }
        };
        
        const options = { 
            nodes: { font: { color: '#eeeeee', strokeWidth: 2, strokeColor: '#121212' } },
            edges: { arrows: 'to', font: { color: '#bbbbbb', strokeWidth: 2, strokeColor: '#121212', align: 'top' } },
            physics: { enabled: false }, 
            interaction: { hover: true },
            layout: { randomSeed: 42 },
            locale: 'en', locales: locales,
            manipulation: window._topologyEnabled ? { 
                enabled: true,
                addNode: function(nodeData, callback) {
                    const modal = bootstrap.Modal.getOrCreateInstance(document.getElementById('addTopologyNodeModal'));
                    const list = document.getElementById('topologyDeviceList');
                    const search = document.getElementById('searchTopologyDevice');
                    
                    const renderList = (filter = '') => {
                        list.innerHTML = '';
                        // Filter out devices already on this tab
                        const existingIds = new Set(nodes.get().map(n => n.id));
                        const available = window.allDevices.filter(d => !existingIds.has(d.id));
                        
                        available.filter(d => d.name.toLowerCase().includes(filter) || d.ip_address.toLowerCase().includes(filter)).forEach(d => {
                            const btn = document.createElement('button');
                            btn.className = 'list-group-item list-group-item-action bg-transparent text-white border-secondary mb-1';
                            btn.innerHTML = `<strong>${escapeHtml(d.name)}</strong> <small class="text-secondary ms-2">${escapeHtml(d.ip_address)}</small>`;
                            btn.onclick = () => {
                                // determine icon code
                                let dt = (d.device_type || "").toLowerCase();
                                let icon_code = "\uf6a6";
                                if(dt.includes("server")) icon_code="\uf52c";
                                else if(dt.includes("router")) icon_code="\uf6ec";
                                else if(dt.includes("switch")) icon_code="\uf40d";
                                else if(dt.includes("firewall")) icon_code="\uf538";
                                else if(dt.includes("database")) icon_code="\uf8c4";
                                
                                nodeData.id = d.id;
                                nodeData.label = `${escapeHtml(d.name)}\n${escapeHtml(d.ip_address)}`;
                                nodeData.shape = 'icon';
                                nodeData.icon = { face: 'bootstrap-icons', code: icon_code, size: 50, color: '#28a745' };
                                nodeData.title = d.remark ? `Remark: ${escapeHtml(d.remark)}` : undefined;
                                callback(nodeData);
                                modal.hide();
                            };
                            list.appendChild(btn);
                        });
                    };
                    
                    search.oninput = (e) => renderList(e.target.value.toLowerCase());
                    renderList();
                    
                    document.getElementById('closeAddNodeModal').onclick = () => { callback(null); modal.hide(); };
                    modal.show();
                },
                editNode: function() { return null; },
                deleteNode: true,
                addEdge: true,
                editEdge: true,
                deleteEdge: true
            } : { enabled: false }
        };
        
        if (window.networkInstance) {
            window.networkInstance.destroy();
        }
        window.networkInstance = new vis.Network(container, { nodes, edges }, options);
        window.networkInstance.once('afterDrawing', () => {
            // Fit all nodes into view (preserves saved layout positioning)
            window.networkInstance.fit();
        });
        window.currentNetworkNodes = nodes;
        window.currentNetworkEdges = edges;
    };

    pageUpdaters.topology = function(isSseUpdate = false) {
        loadAvailableDevices();
        fetch('/api/topology', {credentials: 'include'}).then(r => r.json()).then(data => {
            window.topologyTabs = data.tabs;
            window.topologyNodesMap = {};
            window.topologyEdgesMap = {};
            
            data.tabs.forEach(t => {
                window.topologyNodesMap[t.id] = [];
                window.topologyEdgesMap[t.id] = [];
            });
            
            data.nodes.forEach(n => window.topologyNodesMap[n.tab_id].push(n));
            data.edges.forEach(e => window.topologyEdgesMap[e.tab_id].push(e));
            
            if (!window.currentTopologyTabId && window.topologyTabs.length > 0) {
                window.currentTopologyTabId = window.topologyTabs[0].id;
            }
            if (window.currentTopologyTabId) {
                if (isSseUpdate && window.networkInstance && window.currentNetworkNodes) {
                    // Soft update: just update colors/status of existing nodes to preserve local edits/positions
                    const tabNodes = window.topologyNodesMap[window.currentTopologyTabId] || [];
                    const currentNodes = window.currentNetworkNodes;
                    tabNodes.forEach(n => {
                        const existing = currentNodes.get(n.id);
                        if (existing) {
                            currentNodes.update({
                                id: n.id,
                                color: n.color,
                                font: n.font,
                                title: n.title,
                                icon: n.icon
                            });
                        }
                    });
                } else {
                    switchTopologyTab(window.currentTopologyTabId);
                }
            } else {
                const container = document.getElementById('topology-network');
                if (container) container.innerHTML = '<div class="text-secondary text-center mt-5">No locations available. Click "New Location" to create one.</div>';
                const input = document.getElementById('topologySiteSearchInput');
                if (input) input.value = '';
                renderTopologyTabsList();
            }

            // Wire up Add Tab button
            const btnAddTab = document.getElementById('btnAddTopologyTab');
            if (btnAddTab && !btnAddTab.dataset.bound) {
                btnAddTab.addEventListener('click', () => {
                    document.getElementById('addTopologyTabForm').reset();
                    bootstrap.Modal.getOrCreateInstance(document.getElementById('addTopologyTabModal')).show();
                });
                btnAddTab.dataset.bound = "true";
            }

            const addTabForm = document.getElementById('addTopologyTabForm');
            if (addTabForm && !addTabForm.dataset.bound) {
                addTabForm.addEventListener('submit', (e) => {
                    e.preventDefault();
                    const name = document.getElementById('newTopologyTabName').value.trim();
                    if (name) {
                        authFetch('/api/topology/tab', {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({ name })
                        }).then(r => {
                            if (!r.ok) throw new Error('Failed to create topology tab');
                            return r.json();
                        }).then(tab => {
                            window.topologyTabs.push(tab);
                            window.topologyNodesMap[tab.id] = [];
                            window.topologyEdgesMap[tab.id] = [];
                            const input = document.getElementById('topologySiteSearchInput');
                            if (input) input.value = tab.name;
                            switchTopologyTab(tab.id);
                            bootstrap.Modal.getInstance(document.getElementById('addTopologyTabModal'))?.hide();
                        }).catch(e => showErrorToast(e.message));
                    }
                });
                addTabForm.dataset.bound = "true";
            }

            const btnDeleteTab = document.getElementById('btnDeleteTopologyTab');
            if (btnDeleteTab && !btnDeleteTab.dataset.bound) {
                btnDeleteTab.addEventListener('click', () => {
                    if (!window.currentTopologyTabId) return;
                    window.showConfirmModal(
                        'Delete Topology Tab',
                        'Are you sure you want to delete this topology tab? This cannot be undone.',
                        'Delete Tab',
                        'btn-danger',
                        () => {
            authFetch(`/api/topology/tab/${window.currentTopologyTabId}`, {
                                method: 'DELETE'
                            }).then(r => {
                                if (!r.ok) throw new Error('Failed to delete topology tab');
                                window.currentTopologyTabId = null;
                                pageUpdaters.topology();
                            }).catch(e => showErrorToast(e.message));
                        }
                    );
                });
                btnDeleteTab.dataset.bound = "true";
            }

            // Wire up Save layout
            const btnSave = document.getElementById('btnSaveTopology');
            if (btnSave && !btnSave.dataset.bound) {
                btnSave.addEventListener('click', () => {
                    if (!window.networkInstance || !window.currentTopologyTabId) return;
                    btnSave.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span> Saving...';
                    btnSave.disabled = true;

                    const positions = window.networkInstance.getPositions();
                    const nodesData = window.currentNetworkNodes.get().map(n => ({
                        id: n.id,
                        x: positions[n.id]?.x,
                        y: positions[n.id]?.y
                    }));

                    const edgesData = window.currentNetworkEdges.get().map(e => ({
                        from: e.from,
                        to: e.to,
                        label: e.label || ''
                    }));

                    authFetch('/api/topology/save', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ tab_id: window.currentTopologyTabId, nodes: nodesData, edges: edgesData })
                    }).then(r => {
                        if (!r.ok) throw new Error('Failed to save topology layout');
                        triggerRefresh(true); // soft update preserves canvas + zoom/pan
                        setTimeout(() => {
                            btnSave.innerHTML = '<i class="bi bi-check-lg me-1"></i> Saved';
                            btnSave.disabled = false;
                            setTimeout(() => { btnSave.innerHTML = '<i class="bi bi-floppy me-1"></i> Save Layout'; }, 2000);
                        }, 500);
                    }).catch(e => {
                        showErrorToast(e.message);
                        btnSave.innerHTML = '<i class="bi bi-floppy me-1"></i> Save Layout';
                        btnSave.disabled = false;
                    });
                });
                btnSave.dataset.bound = "true";
            }

            const btnFit = document.getElementById('btnFitTopology');
            if (btnFit && !btnFit.dataset.bound) {
                btnFit.addEventListener('click', () => { 
                    if (window.networkInstance) window.networkInstance.fit(); 
                });
                btnFit.dataset.bound = "true";
            }
            const btnFull = document.getElementById('btnFullscreenTopology');
            if (btnFull && !btnFull.dataset.bound) {
                btnFull.addEventListener('click', () => {
                    const container = document.getElementById('topology-network');
                    if (document.fullscreenElement === container) {
                        document.exitFullscreen();
                    } else {
                        container.requestFullscreen().then(() => {
                            if (window.networkInstance) setTimeout(() => window.networkInstance.fit(), 100);
                        }).catch(() => {});
                    }
                });
                document.addEventListener('fullscreenchange', () => {
                    const inFs = document.fullscreenElement === document.getElementById('topology-network');
                    btnFull.innerHTML = inFs
                        ? '<i class="bi bi-arrows-angle-contract me-1"></i> Exit'
                        : '<i class="bi bi-arrows-angle-expand me-1"></i> Fullscreen';
                    if (!inFs && window.networkInstance) window.networkInstance.fit();
                });
                btnFull.dataset.bound = "true";
            }
            const btnPhys = document.getElementById('btnPhysicsToggle');
            if (btnPhys) {
                const updatePhysicsBtn = () => {
                    const isPhysicsOn = window.networkInstance && window.networkInstance.physics.options.enabled;
                    btnPhys.innerHTML = isPhysicsOn ? '<i class="bi bi-magnet-fill me-1"></i> Physics: ON' : '<i class="bi bi-magnet me-1"></i> Physics: OFF';
                    btnPhys.classList.remove('btn-outline-primary', 'btn-outline-secondary');
                    btnPhys.classList.add(isPhysicsOn ? 'btn-outline-primary' : 'btn-outline-secondary');
                };
                if (!btnPhys.dataset.bound) {
                    btnPhys.addEventListener('click', () => {
                        if (window.networkInstance) {
                            const cur = window.networkInstance.physics.options.enabled;
                            window.networkInstance.setOptions({ physics: { enabled: !cur } });
                            updatePhysicsBtn();
                        }
                    });
                    btnPhys.dataset.bound = "true";
                }
                updatePhysicsBtn();
            }
        });
    };

    // --- DEVICES ---
    window.currentDeviceFilterId = 'all';

    pageUpdaters.devices = function() {
        const searchEl = document.getElementById('devicesPageSearchInput');
        const statusEl = document.getElementById('devicesFilterStatus');
        const subnetEl = document.getElementById('devicesFilterSubnet');
        
        // Update subnet combo box based on all cached devices
        if (subnetEl && window.allDevices) {
            const subnets = new Set();
            window.allDevices.forEach(d => {
                if (d.ip_address) {
                    const parts = d.ip_address.split('.');
                    if (parts.length === 4) subnets.add(`${parts[0]}.${parts[1]}.${parts[2]}.0/24`);
                }
            });
            const availableSubnets = Array.from(subnets).sort();
            const currentStr = availableSubnets.join(',');
            if (subnetEl.dataset.subnets !== currentStr) {
                const currentVal = subnetEl.value;
                let opts = `<option value="all" style="background: #1a1a1a; color: #fff;">All Subnets</option>`;
                availableSubnets.forEach(sub => {
                    opts += `<option value="${escapeHtml(sub)}" style="background: #1a1a1a; color: #fff;">${escapeHtml(sub)}</option>`;
                });
                subnetEl.innerHTML = opts;
                subnetEl.value = availableSubnets.includes(currentVal) ? currentVal : 'all';
                subnetEl.dataset.subnets = currentStr;
            }
        }
        
        let url = '/api/devices/paginated';
        const params = new URLSearchParams();
        
        if (searchEl && searchEl.value) params.append('search', searchEl.value);
        if (statusEl && statusEl.value !== 'all') params.append('status', statusEl.value);
        if (subnetEl && subnetEl.value !== 'all') params.append('subnet', subnetEl.value);
        
        if (params.toString()) {
            url += '?' + params.toString();
        }

        fetchPaginated(url, 'devices', 'devicesPagination', 'devices', (items, total) => {
            const devList = document.getElementById('device-management-list');
            const countSpan = document.getElementById('deviceListCount');
            if (countSpan) countSpan.innerText = `Showing ${total} devices`;
            
            if (!devList) return;
            
            devList.innerHTML = items.map(dev => {
                const enc = encodeURIComponent(JSON.stringify(dev));
                let displayStatus = !dev.enabled ? 'PAUSED' : dev.status;
                const colorClass = displayStatus === 'ONLINE' ? 'success' : (displayStatus === 'OFFLINE' ? 'danger' : (displayStatus === 'PAUSED' ? 'warning' : 'secondary'));
                const indClass = displayStatus === 'ONLINE' ? 'online' : (displayStatus === 'OFFLINE' ? 'offline' : (displayStatus === 'PAUSED' ? 'attention' : 'unknown'));

                const remarkHtml = dev.remark ? ` title="${escapeHtml(dev.remark)}"` : '';
                const remarkStyle = dev.remark ? 'border-bottom: 1px dotted #888; cursor: help;' : '';

                return `
                    <tr style="cursor: pointer;" data-action="open-device" data-device="${enc}">
                        <td class="text-center stop-propagation">
                            ${(window.isReadonly || !window.isAuthenticated) ? '' : '<input class="form-check-input row-checkbox bg-transparent border-secondary shadow-none" type="checkbox" data-id="' + dev.id + '">'}
                        </td>
                        <td class="text-white fw-medium"><span${remarkHtml} style="${remarkStyle}">${escapeHtml(dev.name)}</span></td>
                        <td class="text-secondary">${escapeHtml(dev.device_type)}</td>
                        <td class="text-secondary" style="font-family: monospace;">${escapeHtml(dev.ip_address)}</td>
                        <td class="text-${colorClass} fw-bold"><span class="indicator ind-${indClass}"></span>${escapeHtml(displayStatus)}</td>
                        <td class="text-center" style="vertical-align: middle;">
                            ${(window.isReadonly || !window.isAuthenticated) ? '' : '<i class="bi bi-trash text-danger" style="font-size: 1.15rem; cursor: pointer;" data-action="delete-device" data-id="' + dev.id + '"></i>'}
                        </td>
                    </tr>`;
            }).join('');

            document.querySelectorAll('.row-checkbox').forEach(cb => {
                cb.addEventListener('change', toggleBulkDeleteBtn);
            });
        }, () => {
            const devList = document.getElementById('device-management-list');
            const countSpan = document.getElementById('deviceListCount');
            if (countSpan) countSpan.innerText = `Showing 0 devices`;
            if (devList) devList.innerHTML = '<tr><td colspan="6" class="text-center text-secondary py-4" style="border-color: #222;">No devices found.</td></tr>';
        });
    };

    // --- ALERTS ---
    window.currentAlertDeviceFilterId = 'all';

    pageUpdaters.alerts = function(isSseUpdate = false) {
        let isSingleDevice = (window.currentAlertDeviceFilterId && window.currentAlertDeviceFilterId !== 'all');
        let url = '/api/alerts';
        const params = new URLSearchParams();
        
        if (isSingleDevice) {
            params.append('device_id', window.currentAlertDeviceFilterId);
        }
        const timeFilter = document.getElementById('alertsTimeFilter');
        if (timeFilter && timeFilter.value !== 'all') {
            params.append('time_filter', timeFilter.value);
        }
        const statusFilter = document.getElementById('alertsStatusFilter');
        if (statusFilter && statusFilter.value !== 'all') {
            params.append('status', statusFilter.value);
        }
        
        if (params.toString()) {
            url += '?' + params.toString();
        }

        const accordion = document.getElementById('alertsAccordion');
        if (!accordion) return;

        // Save currently open accordions
        const openIds = new Set();
        accordion.querySelectorAll('.collapse.show').forEach(el => {
            openIds.add(el.id);
        });

        fetchPaginated(url, 'alerts', 'alertsPagination', 'alerts', (events, total) => {
            const grouped = {};
            
            if (isSingleDevice) {
                // If a single device is selected, the server paginated the ALERTS directly.
                if (events.length > 0) {
                    const devId = events[0].device_id;
                    grouped[devId] = { id: devId, device: events[0].device_name, ip: events[0].ip_address, events: events };
                }
            } else {
                // Global view: server paginated the DEVICES.
                events.forEach(alert => {
                    if (!grouped[alert.device_id]) {
                        grouped[alert.device_id] = { id: alert.device_id, device: alert.device_name, ip: alert.ip_address, remark: alert.remark, events: [] };
                    }
                    grouped[alert.device_id].events.push(alert);
                });
            }

            const arr = Object.values(grouped);
            // Sort accordions by most recent event timestamp
            arr.sort((a, b) => new Date(b.events[0].timestamp) - new Date(a.events[0].timestamp));

            let fullHtml = '';
            arr.forEach((devData, index) => {
                const hasEvents = devData.events && devData.events.length > 0;
                const cur = hasEvents ? devData.events[0].alert_type : 'ONLINE';
                const sid = devData.id || index; // fallback
                const stripe = index % 2 === 0 ? 'striped-row' : '';
                
                const getAlertColor = (type) => type === 'OFFLINE' ? 'danger' : (type === 'PAUSED' ? 'warning' : 'success');
                const getIndicatorClass = (type) => type === 'OFFLINE' ? 'offline' : (type === 'PAUSED' ? 'attention' : 'online');
                
                const remarkHtml = devData.remark ? ` title="${escapeHtml(devData.remark)}"` : '';
                const remarkStyle = devData.remark ? 'border-bottom: 1px dotted #888; cursor: help;' : '';

                let html = `
                    <tr class="${stripe}" data-bs-toggle="collapse" data-bs-target="#collapse-${escapeHtml(sid)}" style="cursor: pointer;">
                        <td class="text-white fw-medium ps-3"><span${remarkHtml} style="${remarkStyle}">${escapeHtml(devData.device)}</span></td>
                        <td class="text-secondary" style="font-family: monospace;">${escapeHtml(devData.ip)}</td>
                        <td class="text-${getAlertColor(cur)} fw-bold"><span class="indicator ind-${getIndicatorClass(cur)}"></span>${hasEvents ? escapeHtml(cur) : 'NO ALERTS'}</td>
                    </tr>
                    <tr class="${stripe}"><td colspan="3" class="p-0 border-0"><div class="collapse" id="collapse-${escapeHtml(sid)}" data-bs-parent="#alertsAccordion"><div class="p-4" style="background-color: rgba(255,255,255,0.01); border-bottom: 1px solid #1f1f1f;"><div class="timeline m-0 p-0 ps-3">`;

                if (hasEvents) {
                    devData.events.forEach(evt => {
                        html += window.renderTimelineEvent(evt);
                    });
                    html += `<div class="mt-3 pt-2 border-top border-secondary border-opacity-25">
                                <a href="#" data-action="view-report" data-device-id="${escapeHtml(sid)}" data-device-name="${escapeHtml(devData.device)}" class="text-primary text-decoration-none" style="font-size: 0.85rem; font-weight: 500;">
                                    <i class="bi bi-box-arrow-up-right me-1"></i>View full history in Reports
                                </a>
                             </div>`;
                } else {
                    html += '<div class="text-secondary">No recorded alerts for this period.</div>';
                }

                html += '</div></div></div></td></tr>';
                fullHtml += html;
            });

            // Save scroll position before rebuild
            const savedScroll = document.scrollingElement ? document.scrollingElement.scrollTop : 0;
            accordion.innerHTML = fullHtml;
            document.scrollingElement.scrollTop = savedScroll;

            // Re-open accordions
            openIds.forEach(id => {
                const el = document.getElementById(id);
                if (el) el.classList.add('show');
            });
        }, () => {
            if (accordion) accordion.innerHTML = '<tr><td colspan="3" class="text-center text-secondary py-4" style="border-color: #222;">No alerts found.</td></tr>';
        });
    };

    // --- REPORTS ---
    window.currentReportDeviceFilterId = null;

    pageUpdaters.reports = function() {
        const wrapper = document.getElementById('reportContentWrapper');
        const empty = document.getElementById('reportEmptyState');
        const devId = window.currentReportDeviceFilterId;
        
        if (!devId || devId === 'all') {
            if (wrapper) wrapper.style.display = 'none';
            if (empty) {
                empty.classList.remove('d-none');
                empty.classList.add('d-flex');
            }
            return;
        }

        if (wrapper) wrapper.style.display = 'block';
        if (empty) {
            empty.classList.remove('d-flex');
            empty.classList.add('d-none');
        }

        const timeFilter = document.getElementById('reportTimeFilter').value;
        let url = `/api/reports/ui_data?device_id=${devId}&timeframe=${timeFilter}`;
        if (timeFilter === 'custom') {
            const start = document.getElementById('reportStartDate').value;
            const end = document.getElementById('reportEndDate').value;
            if (start && end) {
                const startIso = new Date(start).toISOString();
                const endIso = new Date(end).toISOString();
                url += `&start_date=${encodeURIComponent(startIso)}&end_date=${encodeURIComponent(endIso)}`;
            } else {
                return; // Wait for user to select dates
            }
        }
        fetch(url, {credentials: 'include'})
            .then(r => r.json())
            .then(data => {
                document.getElementById('reportUptime').innerText = data.global_uptime + '%';
                document.getElementById('reportLatency').innerText = data.avg_latency + 'ms';
                document.getElementById('totalIncidentsKpi').innerText = data.incident_count;
                document.getElementById('reportSla').innerText = data.sla_compliance + '%';

                // Heatmap
                const hmContainer = document.getElementById('uptimeHeatmapContainer');
                if (hmContainer) {
                    hmContainer.innerHTML = '';
                    data.heatmap_blocks.forEach(b => {
                        const block = document.createElement('div');
                        block.className = 'heatmap-block';
                        if (b.uptime >= 100 && !b.has_incident) block.classList.add('heatmap-100');
                        else if (b.uptime >= 95) block.classList.add('heatmap-90');
                        else if (b.uptime >= 85) block.classList.add('heatmap-50');
                        else block.classList.add('heatmap-0');
                        
                        const startObj = new Date(b.start);
                        const endObj = new Date(b.end);
                        block.title = `${startObj.toLocaleString('en-GB', { hour12: true }).toUpperCase()} - ${endObj.toLocaleString('en-GB', { hour12: true }).toUpperCase()}\nUptime: ${b.uptime}%`;
                        hmContainer.appendChild(block);
                    });
                }

                // Timeline
                const tlContainer = document.getElementById('incidentTimeline');
                if (tlContainer) {
                    tlContainer.innerHTML = '<div class="text-secondary fst-italic">Loading timeline...</div>';
                    let alertsUrl = `/api/alerts?device_id=${devId}&time_filter=${timeFilter}`;
                    if (timeFilter === 'custom') {
                        const startVal = document.getElementById('reportStartDate').value;
                        const endVal = document.getElementById('reportEndDate').value;
                        if (startVal && endVal) {
                            const startIso = new Date(startVal).toISOString();
                            const endIso = new Date(endVal).toISOString();
                            alertsUrl += `&start_date=${encodeURIComponent(startIso)}&end_date=${encodeURIComponent(endIso)}`;
                        }
                    }
                    
                    fetchPaginated(alertsUrl, 'reports', 'reportsPagination', 'reports', (events) => {
                        tlContainer.innerHTML = events.map(evt => window.renderTimelineEvent(evt)).join('');
                    }, () => {
                        tlContainer.innerHTML = '<div class="text-secondary fst-italic">No incidents in this timeframe.</div>';
                    });
                }
            }).catch(e => console.error(e));
    };

    // =========================================================
    // Combo List Renderers
    // =========================================================
    function renderComboList(containerId, searchInputId, items, searchKeys, allLabel, onSelect) {
        const container = document.getElementById(containerId);
        const input = document.getElementById(searchInputId);
        if (!container || !input) return;

        const filterText = (input.value || '').toLowerCase();
        container.innerHTML = '';

        if (allLabel) {
            const allItem = document.createElement('a');
            allItem.className = 'dropdown-item text-white rounded combo-item';
            allItem.href = '#';
            allItem.innerText = allLabel;
            allItem.onclick = (e) => { e.preventDefault(); onSelect('all', ''); };
            container.appendChild(allItem);
        }

        let matchCount = 0;
    (items || []).forEach(d => {
            const match = searchKeys.some(key => d[key] && String(d[key]).toLowerCase().includes(filterText));
            if (match) {
                matchCount++;
                const item = document.createElement('a');
                item.className = 'dropdown-item text-white rounded combo-item';
                item.href = '#';
                item.innerText = d.name;
                item.onclick = (e) => { e.preventDefault(); onSelect(d.id, d.name); };
                container.appendChild(item);
            }
        });

        // Show "No results" message if nothing matched
        if (matchCount === 0) {
            const noResults = document.createElement('div');
            noResults.className = 'dropdown-item text-secondary text-center py-2';
            noResults.style.cursor = 'default';
            noResults.innerText = 'No results found';
            container.appendChild(noResults);
        }
    }

    // =========================================================
    // Device Details Panel
    // =========================================================
    window.openDeviceDetails = function(encodedDev) {
        const dev = JSON.parse(decodeURIComponent(encodedDev));
        window.currentOpenDevice = dev;
        let displayStatus = !dev.enabled ? 'PAUSED' : dev.status;
        const colorClass = displayStatus === 'ONLINE' ? 'success' : (displayStatus === 'OFFLINE' ? 'danger' : (displayStatus === 'PAUSED' ? 'warning' : 'secondary'));
        document.getElementById('detailDeviceName').innerText = dev.name;
        document.getElementById('detailDeviceBadge').innerHTML = `<i class="bi bi-circle-fill text-${colorClass} me-1" style="font-size: 0.5rem; vertical-align: middle;"></i> ${escapeHtml(displayStatus)}`;
        
        const filterSelect = document.getElementById('vitalsTimeFilter');
        if (filterSelect) filterSelect.value = '24h';

        const toggleBtn = document.getElementById('btnToggleMonitoring');
        if (toggleBtn) {
            toggleBtn.innerText = !dev.enabled ? 'Resume Monitoring' : 'Pause Monitoring';
            toggleBtn.classList.remove('btn-outline-success', 'btn-outline-warning');
            toggleBtn.classList.add(!dev.enabled ? 'btn-outline-success' : 'btn-outline-warning');
        }

        window.fetchVitals(dev.id, '24h');
        const editName = document.getElementById('editName'); if (editName) editName.value = dev.name;
        const editIp = document.getElementById('editIp'); if (editIp) editIp.value = dev.ip_address;
        const editType = document.getElementById('editType'); if (editType) editType.value = dev.device_type;
        const editSite = document.getElementById('editSite'); if (editSite) editSite.value = dev.site || '';
        const editLocation = document.getElementById('editLocation'); if (editLocation) editLocation.value = dev.location || '';
        const editRack = document.getElementById('editRack'); if (editRack) editRack.value = dev.rack || '';
        const editVendor = document.getElementById('editVendor'); if (editVendor) editVendor.value = dev.vendor || '';
        const editModel = document.getElementById('editModel'); if (editModel) editModel.value = dev.model || '';
        const editInterval = document.getElementById('editInterval'); if (editInterval) editInterval.value = dev.check_interval || 1;
        const editRemark = document.getElementById('editRemark'); if (editRemark) editRemark.value = dev.remark || '';
        const editSnmpVersion = document.getElementById('editSnmpVersion'); if (editSnmpVersion) { editSnmpVersion.value = dev.snmp_version || 'None'; window.toggleSnmpEditFields(); }
        const editSnmpCommunity = document.getElementById('editSnmpCommunity'); if (editSnmpCommunity) { editSnmpCommunity.value = ''; editSnmpCommunity.placeholder = dev.snmp_community ? 'Leave blank to keep current' : 'No community set'; }
        // SNMP v3 fields are not returned by the API (security) — leave blank to keep current
        const editSnmpUser = document.getElementById('editSnmpUser'); if (editSnmpUser) { editSnmpUser.value = ''; editSnmpUser.placeholder = 'Leave blank to keep current'; }
        const editSnmpAuth = document.getElementById('editSnmpAuth'); if (editSnmpAuth) { editSnmpAuth.value = ''; editSnmpAuth.placeholder = 'Leave blank to keep current'; }
        const editSnmpPriv = document.getElementById('editSnmpPriv'); if (editSnmpPriv) { editSnmpPriv.value = ''; editSnmpPriv.placeholder = 'Leave blank to keep current'; }

        const snmpSection = document.getElementById('snmpDetailsSection');
        if (snmpSection) {
            if (dev.snmp_version === 'v2c' || dev.snmp_version === 'v3') {
                snmpSection.style.display = 'block';
                document.getElementById('detailSnmpName').innerText = dev.sys_name || '--';
                document.getElementById('detailSnmpContact').innerText = dev.sys_contact || '--';
                document.getElementById('detailSnmpLocation').innerText = dev.sys_location || '--';
                document.getElementById('detailSnmpUptime').innerText = dev.sys_uptime || '--';
                document.getElementById('detailSnmpDescr').innerText = dev.sys_descr || '--';
                
                const assetSec = document.getElementById('snmpAssetSection');
                if ((dev.client_count !== null && dev.client_count !== undefined) || (dev.ap_count !== null && dev.ap_count !== undefined) || dev.serial_number || dev.snmp_custom_data) {
                    if (assetSec) assetSec.style.display = 'block';
                    
                    const clientBlock = document.getElementById('snmpClientCountBlock');
                    if (dev.client_count !== null && dev.client_count !== undefined) {
                        if (clientBlock) clientBlock.style.display = 'block';
                        document.getElementById('detailSnmpClientCount').innerText = dev.client_count;
                    } else {
                        if (clientBlock) clientBlock.style.display = 'none';
                    }

                    const apBlock = document.getElementById('snmpApCountBlock');
                    if (dev.ap_count !== null && dev.ap_count !== undefined) {
                        if (apBlock) apBlock.style.display = 'block';
                        document.getElementById('detailSnmpApCount').innerText = dev.ap_count;
                    } else {
                        if (apBlock) apBlock.style.display = 'none';
                    }

                    const serialBlock = document.getElementById('snmpSerialNumBlock');
                    if (dev.serial_number) {
                        if (serialBlock) serialBlock.style.display = 'block';
                        document.getElementById('detailSnmpSerialNum').innerText = dev.serial_number;
                    } else {
                        if (serialBlock) serialBlock.style.display = 'none';
                    }

                    const dynFields = document.getElementById('dynamicSnmpFields');
                    if (dynFields) {
                        dynFields.innerHTML = '';
                        if (dev.snmp_custom_data && typeof dev.snmp_custom_data === 'object') {
                            dynFields.innerHTML = Object.keys(dev.snmp_custom_data).map(key => {
                                const val = dev.snmp_custom_data[key];
                                return `
                                    <div class="mt-2">
                                        <div class="text-secondary mb-1" style="font-size: 0.75rem; text-transform: uppercase;">${escapeHtml(key)}</div>
                                        <div class="text-white" style="font-size: 0.9rem;">${escapeHtml(val)}</div>
                                    </div>
                                `;
                            }).join('');
                        }
                    }

                } else {
                    if (assetSec) assetSec.style.display = 'none';
                }
            } else {
                snmpSection.style.display = 'none';
            }
        }

        // Disable all input/select/textarea fields in detail panel when readonly
        const detailReadonly = window.isReadonly || !window.isAuthenticated;
        document.querySelectorAll('#deviceDetailPanel form input, #deviceDetailPanel form select, #deviceDetailPanel form textarea').forEach(f => {
            f.disabled = detailReadonly;
        });

        new bootstrap.Offcanvas(document.getElementById('deviceDetailPanel')).show();
    };

    // Fetch vitals helper
    window.fetchVitals = function(deviceId, timeframe) {
        const latencyEl = document.getElementById('detailLatency');
        const lossEl = document.getElementById('detailLoss');
        const uptimeEl = document.getElementById('detailUptime');
        
        latencyEl.innerHTML = '<span class="spinner-border spinner-border-sm text-secondary"></span>';
        lossEl.innerHTML = '<span class="spinner-border spinner-border-sm text-secondary"></span>';
        if (uptimeEl) uptimeEl.innerHTML = '<span class="spinner-border spinner-border-sm text-secondary"></span>';

        fetch(`/api/devices/${deviceId}/stats?timeframe=${timeframe}`, {credentials: 'include'})
            .then(r => r.json())
            .then(stats => {
                latencyEl.innerText = stats.latency_ms ? stats.latency_ms.toFixed(1) + 'ms' : '--';
                lossEl.innerText = stats.packet_loss !== undefined ? (stats.packet_loss * 100).toFixed(0) + '%' : '--';
                if (uptimeEl) uptimeEl.innerText = stats.uptime_percent !== undefined ? stats.uptime_percent.toFixed(1) + '%' : '--';
            }).catch(() => {
                latencyEl.innerText = 'Err';
                lossEl.innerText = 'Err';
                if (uptimeEl) uptimeEl.innerText = 'Err';
            });
    };

    // Listen for filter changes
    document.addEventListener('change', function(e) {
        if (e.target && e.target.id === 'vitalsTimeFilter') {
            const dev = window.currentOpenDevice;
            if (dev) {
                window.fetchVitals(dev.id, e.target.value);
            }
        }
    });

    // Save config button
    document.addEventListener('click', function(e) {
        if (e.target && e.target.id === 'btnSaveConfig') {
            const dev = window.currentOpenDevice;
            if (!dev) return;
            const btn = e.target;
            btn.disabled = true;
            btn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span> Saving...';
            const snmpCommunityVal = document.getElementById('editSnmpCommunity').value;
            const snmpV3UserVal = document.getElementById('editSnmpUser').value;
            const snmpV3AuthVal = document.getElementById('editSnmpAuth').value;
            const snmpV3PrivVal = document.getElementById('editSnmpPriv').value;
            const updateBody = {
                name: document.getElementById('editName').value,
                ip_address: document.getElementById('editIp').value,
                device_type: document.getElementById('editType').value,
                site: document.getElementById('editSite').value,
                location: document.getElementById('editLocation').value,
                rack: document.getElementById('editRack').value,
                vendor: document.getElementById('editVendor').value,
                model: document.getElementById('editModel').value,
                check_interval: parseInt(document.getElementById('editInterval').value, 10) || 10,
                remark: document.getElementById('editRemark').value,
                snmp_version: document.getElementById('editSnmpVersion').value
            };
            // Only include SNMP credential fields if user entered new values (leave blank to keep current)
            if (snmpCommunityVal) updateBody.snmp_community = snmpCommunityVal;
            if (snmpV3UserVal) updateBody.snmp_v3_user = snmpV3UserVal;
            if (snmpV3AuthVal) updateBody.snmp_v3_auth = snmpV3AuthVal;
            if (snmpV3PrivVal) updateBody.snmp_v3_priv = snmpV3PrivVal;
            authFetch(`/api/devices/${dev.id}`, {
                method: 'PUT',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(updateBody)
            }).then(r => {
                if (!r.ok) throw new Error('Failed to save device configuration');
                return r.json();
            }).then(() => {
                document.getElementById('detailDeviceName').innerText = document.getElementById('editName').value;
                btn.innerText = 'Saved!';
                btn.classList.replace('btn-primary', 'btn-success');
                setTimeout(() => { btn.innerText = 'Save Configuration'; btn.classList.replace('btn-success', 'btn-primary'); btn.disabled = false; }, 2000);
                triggerRefresh(false);
            }).catch(e => {
                showErrorToast(e.message);
                btn.innerText = 'Save Configuration';
                btn.classList.replace('btn-success', 'btn-primary');
                btn.disabled = false;
            });
        } else if (e.target && e.target.id === 'btnToggleMonitoring') {
            const dev = window.currentOpenDevice;
            if (!dev) return;
            const btn = e.target;
            const newEnabled = !dev.enabled ? 1 : 0;
            btn.disabled = true;
            btn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span> Saving...';
            authFetch(`/api/devices/${dev.id}`, {
                method: 'PUT',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ enabled: newEnabled })
            }).then(r => {
                if (!r.ok) throw new Error('Failed to update device monitoring state');
                dev.enabled = newEnabled;

                let displayStatus = !dev.enabled ? 'PAUSED' : dev.status;
                const colorClass = displayStatus === 'ONLINE' ? 'success' : (displayStatus === 'OFFLINE' ? 'danger' : (displayStatus === 'PAUSED' ? 'warning' : 'secondary'));
                document.getElementById('detailDeviceBadge').innerHTML = `<i class="bi bi-circle-fill text-${colorClass} me-1" style="font-size: 0.5rem; vertical-align: middle;"></i> ${escapeHtml(displayStatus)}`;

                btn.innerText = !dev.enabled ? 'Resume Monitoring' : 'Pause Monitoring';
                btn.className = !dev.enabled ? 'btn btn-outline-success w-100 py-2' : 'btn btn-outline-warning w-100 py-2';
                btn.disabled = false;

                triggerRefresh(false);
            }).catch(e => {
                showErrorToast(e.message);
                btn.innerText = !dev.enabled ? 'Resume Monitoring' : 'Pause Monitoring';
                btn.className = !dev.enabled ? 'btn btn-outline-success w-100 py-2' : 'btn btn-outline-warning w-100 py-2';
                btn.disabled = false;
            });
        }
    });

    // =========================================================
    // Delete Device
    // =========================================================
    window.deleteDevice = function(deviceId) {
        window.showConfirmModal(
            'Delete Device',
            'Are you sure you want to delete this device? All associated history will be lost.',
            'Delete',
            'btn-danger',
            () => {
                authFetch(`/api/devices/${deviceId}`, { method: 'DELETE' }).then(r => {
                    if (!r.ok) throw new Error('Failed to delete device');
                    triggerRefresh(false);
                }).catch(e => showErrorToast(e.message));
            }
        );
    };

    // Bulk delete
    function toggleBulkDeleteBtn() {
        const btn = document.getElementById('bulkDeleteBtn');
        const checked = document.querySelectorAll('.row-checkbox:checked');
        const blocked = window.isReadonly || !window.isAuthenticated;
        if (btn) {
            if (!blocked && checked.length > 0) { btn.classList.remove('d-none'); btn.classList.add('d-flex'); }
            else { btn.classList.add('d-none'); btn.classList.remove('d-flex'); }
        }
    }

    // =========================================================
    // Init — wire up all event listeners (runs once)
    // =========================================================
    function init() {
        // Event delegation for data-action and data-nav attributes
        document.addEventListener('click', function(e) {
            const target = e.target.closest('[data-action]');
            if (!target) {
                // Handle data-nav (navigation icons)
                const navTarget = e.target.closest('[data-nav]');
                if (navTarget) {
                    e.preventDefault();
                    navigateTo(navTarget.getAttribute('data-nav'));
                }
                return;
            }

            const action = target.getAttribute('data-action');
            switch (action) {
                case 'export-csv':
                    window.location.href = '/api/devices/export/csv';
                    break;
                case 'change-password':
                    window.doChangePassword();
                    break;
                case 'login':
                    window.doLogin();
                    break;
                case 'paginate':
                    window.paginationState[target.getAttribute('data-page-id')] = parseInt(target.getAttribute('data-page'), 10);
                    const updater = target.getAttribute('data-updater');
                    if (window.pageUpdaters[updater]) window.pageUpdaters[updater]();
                    break;
                case 'open-device':
                    window.openDeviceDetails(target.getAttribute('data-device'));
                    break;
                case 'delete-device':
                    window.deleteDevice(target.getAttribute('data-id'));
                    break;
                case 'view-report':
                    window.currentReportDeviceFilterId = target.getAttribute('data-device-id');
                    document.getElementById('reportDeviceSearchInput').value = target.getAttribute('data-device-name');
                    window.pageUpdaters.reports();
                    e.preventDefault();
                    break;
            }
        });

        // Handle Enter key on password fields
        document.addEventListener('keydown', function(e) {
            if (e.key === 'Enter') {
                const target = e.target.closest('[data-action]');
                if (!target) return;
                const action = target.getAttribute('data-action');
                if (action === 'change-password-on-enter') {
                    window.doChangePassword();
                } else if (action === 'login-on-enter') {
                    window.doLogin();
                }
            }
        });

        // Handle data-clear-search
        document.addEventListener('click', function(e) {
            const btn = e.target.closest('[data-clear-search]');
            if (btn) {
                const inputId = btn.getAttribute('data-clear-search');
                const input = document.getElementById(inputId);
                if (input) {
                    input.value = '';
                    input.focus();
                    input.dispatchEvent(new Event('input', { bubbles: true }));
                }
            }
        });

        // Stop propagation for checkbox cells in device rows
        document.addEventListener('click', function(e) {
            if (e.target.closest('.stop-propagation')) {
                e.stopPropagation();
            }
        });

        // Combobox UX enhancements: Clear text on open, restore on close
        ['topologySiteSearchInput', 'devicesPageSearchInput', 'alertsPageSearchInput', 'reportDeviceSearchInput'].forEach(id => {
            const input = document.getElementById(id);
            if (input) {
                input.addEventListener('show.bs.dropdown', () => {
                    input.dataset.oldValue = input.value;
                    input.value = '';
                    input.dispatchEvent(new Event('input', { bubbles: true }));
                });
                input.addEventListener('hide.bs.dropdown', () => {
                    setTimeout(() => {
                        if (input.value === '') {
                            input.value = input.dataset.oldValue || '';
                        }
                    }, 150);
                });
            }
        });

        // Device filters
        const statusFilter = document.getElementById('devicesFilterStatus');
        if (statusFilter) statusFilter.addEventListener('change', () => pageUpdaters.devices());
        const subnetFilter = document.getElementById('devicesFilterSubnet');
        if (subnetFilter) subnetFilter.addEventListener('change', () => pageUpdaters.devices());

        // Devices search combo
        const dSearch = document.getElementById('devicesPageSearchInput');
        if (dSearch) dSearch.addEventListener('input', debounce(() => {
            if (dSearch.value === '') {
                window.currentDeviceFilterId = 'all';
                pageUpdaters.devices();
            }
            renderComboList('devicesPageComboList', 'devicesPageSearchInput', window.allDevices, ['name', 'ip_address'], 'All Devices', (id, name) => {
                window.currentDeviceFilterId = id;
                dSearch.value = id === 'all' ? '' : name;
                pageUpdaters.devices();
            });
        }));

        // Alerts search combo
        const aSearch = document.getElementById('alertsPageSearchInput');
        if (aSearch) aSearch.addEventListener('input', debounce(() => {
            if (aSearch.value === '') {
                window.currentAlertDeviceFilterId = 'all';
                pageUpdaters.alerts();
            }
            renderComboList('alertsPageComboList', 'alertsPageSearchInput', window.allDevices, ['name', 'ip_address'], 'All Devices', (id, name) => {
                window.currentAlertDeviceFilterId = id;
                aSearch.value = id === 'all' ? '' : name;
                pageUpdaters.alerts();
            });
        }));

        // Reports search combo
        const rSearch = document.getElementById('reportDeviceSearchInput');
        if (rSearch) rSearch.addEventListener('input', debounce(() => {
            if (rSearch.value === '') {
                window.currentReportDeviceFilterId = 'all';
                pageUpdaters.reports();
            }
            renderComboList('reportDeviceComboList', 'reportDeviceSearchInput', window.allDevices, ['name', 'ip_address'], 'All Devices', (id, name) => {
                window.currentReportDeviceFilterId = id;
                rSearch.value = id === 'all' ? '' : name;
                pageUpdaters.reports();
            });
        }));

        // Alerts page filters
        const alertsTimeFilter = document.getElementById('alertsTimeFilter');
        if (alertsTimeFilter) alertsTimeFilter.addEventListener('change', pageUpdaters.alerts);
        const alertsStatusFilter = document.getElementById('alertsStatusFilter');
        if (alertsStatusFilter) alertsStatusFilter.addEventListener('change', pageUpdaters.alerts);

        // Reports Time Filter
        const reportTimeFilter = document.getElementById('reportTimeFilter');
        if (reportTimeFilter) {
            reportTimeFilter.addEventListener('change', () => {
                const customContainer = document.getElementById('reportCustomDateContainer');
                if (reportTimeFilter.value === 'custom') {
                    customContainer.classList.remove('d-none');
                    customContainer.classList.add('d-flex');
                } else {
                    customContainer.classList.remove('d-flex');
                    customContainer.classList.add('d-none');
                    pageUpdaters.reports();
                }
            });
        }

        const btnApplyCustomDate = document.getElementById('btnApplyCustomDate');
        if (btnApplyCustomDate) {
            btnApplyCustomDate.addEventListener('click', pageUpdaters.reports);
        }

        // PDF export
        const pdfBtn = document.getElementById('btnExportPdf');
        if (pdfBtn) pdfBtn.addEventListener('click', () => {
            if (window.currentReportDeviceFilterId && window.currentReportDeviceFilterId !== 'all') {
                const timeFilter = document.getElementById('reportTimeFilter').value;
                let url = `/api/reports/generate?device_id=${window.currentReportDeviceFilterId}&time_filter=${timeFilter}`;
                if (timeFilter === 'custom') {
                    const start = document.getElementById('reportStartDate').value;
                    const end = document.getElementById('reportEndDate').value;
                    if (start && end) {
                        const startIso = new Date(start).toISOString();
                        const endIso = new Date(end).toISOString();
                        url += `&start_date=${encodeURIComponent(startIso)}&end_date=${encodeURIComponent(endIso)}`;
                    } else {
                        alert("Please select both start and end dates.");
                        return;
                    }
                }
                window.location.href = url;
            }
        });

        // Select all checkbox (devices)
        const selectAll = document.getElementById('selectAll');
        if (selectAll) selectAll.addEventListener('change', (e) => {
            document.querySelectorAll('.row-checkbox').forEach(cb => cb.checked = e.target.checked);
            toggleBulkDeleteBtn();
        });

        // Bulk delete button
        const bulkBtn = document.getElementById('bulkDeleteBtn');
        if (bulkBtn) bulkBtn.addEventListener('click', () => {
            window.showConfirmModal(
                'Delete Multiple Devices',
                'Are you sure you want to delete all selected devices?',
                'Delete All',
                'btn-danger',
                () => {
                    const checked = document.querySelectorAll('.row-checkbox:checked');
                    const promises = [];
                    checked.forEach(cb => promises.push(authFetch(`/api/devices/${cb.getAttribute('data-id')}`, { method: 'DELETE' })));
                    Promise.all(promises).then(results => {
                        const failed = results.filter(r => !r.ok).length;
                        if (failed > 0) {
                            showErrorToast(`${failed} device(s) failed to delete`);
                        }
                        triggerRefresh(false);
                        bulkBtn.classList.add('d-none');
                        bulkBtn.classList.remove('d-flex');
                    }).catch(e => showErrorToast(e.message));
                }
            );
        });

        // Add device form
        const addForm = document.getElementById('addDeviceForm');
        if (addForm) addForm.addEventListener('submit', function(e) {
            e.preventDefault();
            let name = document.getElementById('newDeviceName').value.trim();
            const ip = document.getElementById('newDeviceIp').value.trim();
            const type = document.getElementById('newDeviceType').value;
            const interval = document.getElementById('newDeviceInterval').value;
            const site = document.getElementById('newDeviceSite').value.trim();
            const location = document.getElementById('newDeviceLocation').value.trim();
            const rack = document.getElementById('newDeviceRack').value.trim();
            const vendor = document.getElementById('newDeviceVendor').value.trim();
            const model = document.getElementById('newDeviceModel').value.trim();
            const remark = document.getElementById('newDeviceRemark').value;
            const snmp_version = document.getElementById('newDeviceSnmpVersion').value;
            const snmp_community = document.getElementById('newDeviceSnmpCommunity').value;
            const snmp_v3_user = document.getElementById('newDeviceSnmpUser').value;
            const snmp_v3_auth = document.getElementById('newDeviceSnmpAuth').value;
            const snmp_v3_priv = document.getElementById('newDeviceSnmpPriv').value;

            if (!name) name = ip;
            const submitBtn = addForm.querySelector('button[type="submit"]');
            if (submitBtn) { submitBtn.disabled = true; submitBtn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span> Adding...'; }
            authFetch('/api/devices', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    name,
                    ip_address: ip,
                    device_type: type,
                    site,
                    location,
                    rack,
                    vendor,
                    model,
                    check_interval: parseInt(interval, 10) || 10,
                    remark,
                    snmp_version,
                    snmp_community,
                    snmp_v3_user,
                    snmp_v3_auth,
                    snmp_v3_priv
                })
            }).then(r => {
                if (!r.ok) throw new Error('Failed to add device');
                triggerRefresh(false);
                bootstrap.Modal.getInstance(document.getElementById('addDeviceModal'))?.hide();
                addForm.reset();
                window.toggleSnmpFields();
            }).catch(e => {
                showErrorToast(e.message);
                if (submitBtn) { submitBtn.disabled = false; submitBtn.innerHTML = 'Save Device'; }
            });
        });

        // Auto-set interval based on device type (IT Asset Management Standards)
        const getDefaultInterval = (type) => {
            type = (type || '').toLowerCase();
            // Core Network & Servers (1 min)
            if (['router', 'switch', 'gateway', 'firewall', 'security appliance', 'load balancer', 'wireless controller (wlc)', 'controller', 'server', 'hypervisor', 'database', 'virtual machine', 'modem'].includes(type)) return 60;
            // Infrastructure (2 mins)
            if (['access point', 'ups', 'pdu', 'storage/nas', 'ip camera', 'door access control', 'environmental sensor'].includes(type)) return 120;
            // Endpoints & Peripherals (5 mins)
            if (['workstation', 'laptop', 'thin client', 'voip phone', 'printer', 'iot device', 'device'].includes(type)) return 300;
            return 60;
        };
        document.addEventListener('change', (e) => {
            if (e.target && e.target.id === 'newDeviceType') {
                const intInput = document.getElementById('newDeviceInterval');
                if (intInput) intInput.value = getDefaultInterval(e.target.value);
            } else if (e.target && e.target.id === 'editType') {
                const intInput = document.getElementById('editInterval');
                if (intInput) intInput.value = getDefaultInterval(e.target.value);
            }
        });

        // Discover LAN modal
        const discoverModal = document.getElementById('discoverLanModal');
        if (discoverModal) {
            discoverModal.addEventListener('show.bs.modal', () => {
                document.getElementById('discoverState0').classList.remove('d-none');
                document.getElementById('discoverState1').classList.add('d-none');
                document.getElementById('discoverState2').classList.add('d-none');
                document.getElementById('discoverState3').classList.add('d-none');
                const rl = document.getElementById('discoverResultsList'); if (rl) rl.innerHTML = '';
                const dl = document.getElementById('autoDetectedSubnets'); if (dl) dl.innerHTML = '';
                const si = document.getElementById('discoverSubnet'); if (si) si.value = '';

                fetch('/api/devices/subnets', {credentials: 'include'}).then(r => r.json()).then(data => {
                    if (data.subnets && data.subnets.length > 0) {
                        data.subnets.forEach(sub => { if (dl) dl.appendChild(Object.assign(document.createElement('option'), { value: sub, textContent: sub })); });
                        if (si) si.value = data.subnets[0];
                    }
                    document.getElementById('discoverState0').classList.add('d-none');
                    document.getElementById('discoverState1').classList.remove('d-none');
                }).catch(() => {
                    document.getElementById('discoverState0').classList.add('d-none');
                    document.getElementById('discoverState1').classList.remove('d-none');
                });
            });

            document.getElementById('btnStartDiscovery').addEventListener('click', () => {
                const subnet = document.getElementById('discoverSubnet').value.trim();
                if (!subnet) { showErrorToast('Please enter a subnet.'); return; }
                document.getElementById('discoverState1').classList.add('d-none');
                document.getElementById('discoverState2').classList.remove('d-none');
                document.getElementById('discoveryProgressBar').style.width = '50%';
                authFetch('/api/devices/discover', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ subnet })
                }).then(r => {
                    if (!r.ok) throw new Error('Failed to start discovery');
                }).catch(err => {
                    console.error('Discovery error:', err);
                    showErrorToast(err.message || 'Discovery failed — check server logs');
                    document.getElementById('discoverState2').classList.add('d-none');
                    document.getElementById('discoverState3').classList.remove('d-none');
                    document.getElementById('discoveredCount').innerText = '0';
                    document.getElementById('discoverResultsList').innerHTML = '<tr><td colspan="4" class="text-danger text-center">Discovery failed — check server logs</td></tr>';
                });
            });

            document.getElementById('btnImportDiscovered').addEventListener('click', () => {
                const checked = document.querySelectorAll('.discover-checkbox:checked');
                const devicesToAdd = [];
                checked.forEach(cb => {
                    const dev = window.discoveredDevices[cb.getAttribute('data-idx')];
                    devicesToAdd.push({ name: dev.host, ip_address: dev.ip, device_type: 'Device' });
                });

                if (devicesToAdd.length === 0) return;

                const importBtn = document.getElementById('btnImportDiscovered');
                if (importBtn) { importBtn.disabled = true; importBtn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span> Importing...'; }
                authFetch('/api/devices/bulk', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(devicesToAdd)
                }).then(r => {
                    if (!r.ok) throw new Error('Failed to import devices');
                    triggerRefresh(false);
                    bootstrap.Modal.getInstance(discoverModal)?.hide();
                }).catch(e => {
                    showErrorToast(e.message);
                    if (importBtn) { importBtn.disabled = false; importBtn.innerHTML = 'Import Selected Devices'; }
                });
            });
        }

        // Listen for back/forward navigation
        window.addEventListener('popstate', handleRoute);

        // Initial route
        loadAvailableDevices();
        handleRoute();
    }

    // =========================================================
    // Discover Complete handler (called from SSE)
    // =========================================================
    function handleDiscoverComplete(data) {
        document.getElementById('discoveryProgressBar').style.width = '100%';
        setTimeout(() => {
            document.getElementById('discoverState2').classList.add('d-none');
            document.getElementById('discoverState3').classList.remove('d-none');
            const activeIps = data.active_ips || [];
            document.getElementById('discoveredCount').innerText = activeIps.length;
            const list = document.getElementById('discoverResultsList');
            window.discoveredDevices = activeIps.map(ip => ({ ip, host: ip, man: 'Unknown' }));
            list.innerHTML = window.discoveredDevices.map((dev, idx) =>
                `<tr><td class="ps-3 border-secondary"><input class="form-check-input discover-checkbox bg-transparent border-secondary shadow-none" type="checkbox" data-idx="${idx}"></td><td class="text-secondary border-secondary" style="font-family: monospace;">${escapeHtml(dev.ip)}</td><td class="text-white border-secondary">${escapeHtml(dev.host)}</td><td class="text-secondary border-secondary">${escapeHtml(dev.man)}</td></tr>`
            ).join('');
            const selAll = document.getElementById('discoverSelectAll');
            if (selAll) {
                selAll.checked = false;
                selAll.onchange = (e) => document.querySelectorAll('.discover-checkbox').forEach(cb => cb.checked = e.target.checked);
            }
        }, 500);
    }

    // =========================================================
    // Boot
    // =========================================================
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
