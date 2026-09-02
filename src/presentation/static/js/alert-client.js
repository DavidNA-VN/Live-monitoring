/**
 * AlertClient: Quản lý WebSocket realtime và đồng bộ recent alert history.
 * Bounded reconnect backoff (1s -> 15s max), token-based isolation giữa các session,
 * đọc AlertMessageDTO envelope (message.payload), và deduplicate theo alert_id.
 */
export class AlertClient {
    constructor({ apiClient, onAlert = null, onStatusChange = null }) {
        this.apiClient = apiClient;
        this.onAlert = onAlert;
        this.onStatusChange = onStatusChange;

        this.streamId = null;
        this.generationToken = 0;
        this.ws = null;
        this.shouldReconnect = false;
        this.reconnectAttempts = 0;
        this.reconnectTimer = null;
        this.seenAlertIds = new Set();
        this.maxSeenIds = 1000;
    }

    start(streamId) {
        this.stop();
        this.streamId = streamId;
        this.generationToken++;
        this.shouldReconnect = true;
        this.reconnectAttempts = 0;
        this.seenAlertIds.clear();

        const token = this.generationToken;
        this._fetchRecentAlerts(token);
        this._connect();
    }

    _connect() {
        if (!this.streamId || !this.shouldReconnect) return;

        const token = this.generationToken;
        if (this.reconnectTimer) {
            clearTimeout(this.reconnectTimer);
            this.reconnectTimer = null;
        }

        if (this.onStatusChange) {
            this.onStatusChange('CONNECTING');
        }

        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const wsUrl = `${protocol}//${window.location.host}/api/v1/ws/streams/${encodeURIComponent(this.streamId)}`;

        try {
            this.ws = new WebSocket(wsUrl);
        } catch (err) {
            this._scheduleReconnect(token);
            return;
        }
        const socket = this.ws;

        socket.onopen = () => {
            if (this.generationToken !== token) {
                try { socket.close(); } catch {}
                return;
            }
            this.reconnectAttempts = 0;
            if (this.onStatusChange) {
                this.onStatusChange('CONNECTED');
            }
            // Fetch recent history để lấp khoảng trống kết nối
            this._fetchRecentAlerts(token);
        };

        socket.onmessage = (event) => {
            if (this.generationToken !== token) return;
            try {
                const data = JSON.parse(event.data);
                // Đọc AlertMessageDTO envelope
                const alert = (data && data.message_type === 'ALERT' && data.payload) ? data.payload : data;
                if (alert && alert.alert_id) {
                    this._processAlert(alert);
                }
            } catch (err) {
                console.warn("Failed to parse incoming WebSocket message:", err);
            }
        };

        socket.onclose = () => {
            if (this.generationToken !== token) return;
            if (this.ws === socket) this.ws = null;
            if (this.onStatusChange) {
                this.onStatusChange('DISCONNECTED');
            }
            if (this.shouldReconnect) {
                this._scheduleReconnect(token);
            }
        };

        socket.onerror = () => {
            if (this.generationToken !== token) return;
            try { socket.close(); } catch {}
        };
    }

    _scheduleReconnect(token) {
        if (!this.shouldReconnect || this.generationToken !== token) return;

        const delay = Math.min(1000 * Math.pow(2, this.reconnectAttempts), 15000);
        this.reconnectAttempts++;

        this.reconnectTimer = setTimeout(() => {
            if (this.shouldReconnect && this.generationToken === token) {
                this._connect();
            }
        }, delay);
    }

    async _fetchRecentAlerts(token) {
        if (!this.streamId) return;
        const res = await this.apiClient.getRecentAlerts(this.streamId, 50);
        if (this.generationToken !== token) return;

        if (res.ok && Array.isArray(res.data)) {
            for (const alert of res.data) {
                this._processAlert(alert);
            }
        }
    }

    _processAlert(alert) {
        if (!alert || !alert.alert_id) return;
        if (this.seenAlertIds.has(alert.alert_id)) {
            return; // Deduplicate
        }

        this.seenAlertIds.add(alert.alert_id);
        if (this.seenAlertIds.size > this.maxSeenIds) {
            // Trim older IDs
            const first = this.seenAlertIds.values().next().value;
            this.seenAlertIds.delete(first);
        }

        if (this.onAlert) {
            this.onAlert(alert);
        }
    }

    stop() {
        this.shouldReconnect = false;
        this.generationToken++;

        if (this.reconnectTimer) {
            clearTimeout(this.reconnectTimer);
            this.reconnectTimer = null;
        }

        if (this.ws) {
            try {
                this.ws.close();
            } catch {}
            this.ws = null;
        }

        if (this.onStatusChange) {
            this.onStatusChange('STANDBY');
        }
    }
}
