/* Shared SSE connection manager for Network Monitor.
 * Used by both the main SPA and the wall display.
 * Include BEFORE app.js / wall.js.
 * Provides exponential-backoff reconnect and denial handling.
 */
window.createSSEManager = function(opts) {
    var url = opts.url;
    var events = opts.events || {};
    var onConnected = opts.onConnected || function(){};
    var onDenied = opts.onDenied || function(){};
    var onError = opts.onError || function(){};
    var baseDelay = opts.reconnectDelay || 5000;

    var source = null;
    var timer = null;
    var denied = false;
    var attempts = 0;
    var connecting = false;

    function connect() {
        if (denied) return;
        if (connecting) return; // Prevent concurrent connection attempts
        connecting = true;
        if (source) { source.close(); source = null; }
        source = new EventSource(url);

        source.onopen = function() {
            attempts = 0;
            connecting = false;
            onConnected();
        };

        Object.keys(events).forEach(function(name) {
            source.addEventListener(name, function(e) {
                try { events[name](JSON.parse(e.data)); } catch (err) {}
            });
        });

        source.addEventListener("connection_denied", function(e) {
            try {
                var d = JSON.parse(e.data);
                denied = true;
                connecting = false;
                source.close();
                source = null;
                onDenied(d);
            } catch (err) {}
        });

        source.onerror = function() {
            if (denied) return;
            connecting = false;
            if (source) { source.close(); source = null; }
            onError();
            if (timer) clearTimeout(timer);
            var delay = Math.min(baseDelay * Math.pow(1.5, attempts), 30000);
            attempts++;
            timer = setTimeout(connect, delay);
        };
    }

    var manager = {
        connect: connect,
        disconnect: function() {
            denied = true;
            connecting = false;
            if (source) { source.close(); source = null; }
            if (timer) { clearTimeout(timer); timer = null; }
        }
    };
    return manager;
};
