/**
 * DashboardView: Quản lý và cập nhật DOM giao diện.
 * Tuyệt đối an toàn chống XSS: chỉ sử dụng textContent và DOM APIs (không dùng innerHTML).
 */
export class DashboardView {
    constructor() {
        this.streamForm = document.getElementById('stream-form');
        this.streamIdInput = document.getElementById('stream-id');
        this.masterUrlInput = document.getElementById('master-url');
        this.startBtn = document.getElementById('btn-start');
        this.pauseBtn = document.getElementById('btn-pause');
        this.resumeBtn = document.getElementById('btn-resume');
        this.stopBtn = document.getElementById('btn-stop');

        this.modeBadge = document.getElementById('mode-badge');
        this.wsStatusDot = document.querySelector('.connection-status .dot');
        this.wsStatusText = document.getElementById('ws-status-text');

        this.videoEl = document.getElementById('live-video');
        this.audioLevelBar = document.getElementById('audio-level');
        this.eventLogContainer = document.getElementById('event-log-container');

        this.statStatus = document.querySelector('#stat-status .val');
        this.statRes = document.querySelector('#stat-resolution .val');
        this.statFps = document.querySelector('#stat-fps .val');
        this.statBitrate = document.querySelector('#stat-bitrate .val');
    }

    setModeBadge(mode) {
        if (!this.modeBadge) return;
        const upper = (mode || 'unknown').toUpperCase();
        this.modeBadge.textContent = `MODE: ${upper}`;
        const modeClass = mode === 'redis' ? 'redis' : (mode === 'fake' ? 'fake' : 'unknown');
        this.modeBadge.className = `mode-tag mode-${modeClass}`;
    }

    setConnectionStatus(status) {
        if (!this.wsStatusDot || !this.wsStatusText) return;
        const norm = (status || 'STANDBY').toUpperCase();
        this.wsStatusText.textContent = norm;

        this.wsStatusDot.className = 'dot';
        if (norm === 'CONNECTED') {
            this.wsStatusDot.classList.add('connected');
        } else if (norm === 'CONNECTING') {
            this.wsStatusDot.classList.add('connecting');
        } else if (norm === 'DISCONNECTED') {
            this.wsStatusDot.classList.add('disconnected');
        } else {
            this.wsStatusDot.classList.add('standby');
        }
    }

    setButtonsState(state) {
        const isIdle = state === 'IDLE';
        const isRunning = state === 'RUNNING';
        const isPaused = state === 'PAUSED';
        const isTransitioning = state.startsWith('SUBMITTING_') || state === 'WAITING_START_RESULT' || state === 'STARTING';

        this.streamIdInput.disabled = !isIdle;
        this.masterUrlInput.disabled = !isIdle;

        this.startBtn.disabled = !isIdle;
        this.pauseBtn.disabled = !isRunning;
        this.resumeBtn.disabled = !isPaused;
        this.stopBtn.disabled = !(isRunning || isPaused || isTransitioning || state === 'FAILED');

        if (state === 'SUBMITTING_START' || state === 'WAITING_START_RESULT' || state === 'STARTING') {
            this.startBtn.textContent = 'Starting...';
        } else {
            this.startBtn.textContent = 'Connect';
        }
    }

    setSystemStatus(statusText, color = null) {
        if (!this.statStatus) return;
        this.statStatus.textContent = statusText || 'UNKNOWN';
        if (color) {
            this.statStatus.style.color = color;
        } else {
            this.statStatus.style.color = statusText === 'RUNNING' ? 'var(--success)' : 'var(--text-secondary)';
        }
    }

    setTelemetry({ resolution, fps, bitrate }) {
        if (this.statRes && resolution !== undefined) {
            this.statRes.textContent = resolution || '--';
        }
        if (this.statFps && fps !== undefined) {
            this.statFps.textContent = fps !== null ? fps : '--';
        }
        if (this.statBitrate && bitrate !== undefined) {
            this.statBitrate.textContent = bitrate !== null ? bitrate : '--';
        }
    }

    resetTelemetry() {
        this.setSystemStatus('IDLE');
        this.setTelemetry({ resolution: '--', fps: '--', bitrate: '--' });
        if (this.audioLevelBar) {
            this.audioLevelBar.style.height = '0%';
        }
    }

    addSystemLog(text, level = 'info') {
        const div = document.createElement('div');
        div.className = `log-entry ${level === 'error' ? 'error' : (level === 'warning' ? 'warning' : 'system-msg')}`;

        const timeSpan = document.createElement('span');
        timeSpan.className = 'log-time';
        timeSpan.textContent = `[${new Date().toLocaleTimeString('vi-VN', { hour12: false })}]`;

        const msgSpan = document.createElement('span');
        msgSpan.textContent = text;

        div.append(timeSpan, msgSpan);
        this.eventLogContainer.append(div);
        this.eventLogContainer.scrollTop = this.eventLogContainer.scrollHeight;
    }

    addAlertLog(alert) {
        const div = document.createElement('div');
        const state = (alert.state || 'OPEN').toUpperCase();
        const eventType = (alert.event_type || 'ALERT').toUpperCase();

        if (state === 'RESOLVED' || state === 'RECOVERED') {
            div.className = 'log-entry resolved';
        } else if (state === 'DEGRADED') {
            div.className = 'log-entry warning';
        } else {
            div.className = 'log-entry error';
        }

        const timeSpan = document.createElement('span');
        timeSpan.className = 'log-time';
        const timeStr = alert.occurred_at ? new Date(alert.occurred_at).toLocaleTimeString('vi-VN', { hour12: false }) : new Date().toLocaleTimeString('vi-VN', { hour12: false });
        timeSpan.textContent = `[${timeStr}]`;

        // State Badge
        const stateBadge = document.createElement('span');
        stateBadge.className = `badge badge-${state.toLowerCase()}`;
        stateBadge.textContent = state;

        // Type Badge
        const typeBadge = document.createElement('span');
        typeBadge.className = 'badge badge-type';
        typeBadge.textContent = eventType;

        // Reason text
        const reasonSpan = document.createElement('span');
        reasonSpan.textContent = alert.reason || (eventType === 'BLACK_SCREEN' ? 'Màn hình đen' : 'Mất âm thanh');

        div.append(timeSpan, stateBadge, typeBadge, reasonSpan);
        this.eventLogContainer.append(div);
        this.eventLogContainer.scrollTop = this.eventLogContainer.scrollHeight;
    }
}
