import { presentAlert } from './alert-presentation.js?v=6.0';

export class DashboardView {
    constructor() {
        this.streamForm = document.getElementById('stream-form');
        this.streamIdInput = document.getElementById('stream-id');
        this.masterUrlInput = document.getElementById('master-url');
        this.variantSelectionInput = document.getElementById(
            'variant-selection'
        );
        this.freezeEnabledInput = document.getElementById('check-video-freeze');
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
        this.statAdmissionMode = document.querySelector(
            '#stat-admission-mode .val'
        );
        this.statQueueLag = document.querySelector('#stat-queue-lag .val');
        this.statCoverageGaps = document.querySelector(
            '#stat-coverage-gaps .val'
        );
        this.statDroppedSegments = document.querySelector(
            '#stat-dropped-segments .val'
        );
        this.statMediaProcesses = document.querySelector(
            '#stat-media-processes .val'
        );
    }

    setModeBadge(mode) {
        if (!this.modeBadge) return;
        const normalized = mode || 'unknown';
        this.modeBadge.textContent = `MODE: ${normalized.toUpperCase()}`;
        const modeClass = normalized === 'redis'
            ? 'redis'
            : (normalized === 'fake' ? 'fake' : 'unknown');
        this.modeBadge.className = `mode-tag mode-${modeClass}`;
    }

    setConnectionStatus(status) {
        if (!this.wsStatusDot || !this.wsStatusText) return;
        const normalized = (status || 'STANDBY').toUpperCase();
        this.wsStatusText.textContent = normalized;
        this.wsStatusDot.className = 'dot';
        const statusClass = ['CONNECTED', 'CONNECTING', 'DISCONNECTED']
            .includes(normalized)
            ? normalized.toLowerCase()
            : 'standby';
        this.wsStatusDot.classList.add(statusClass);
    }

    setButtonsState(state) {
        const isIdle = state === 'IDLE';
        const isRunning = state === 'RUNNING';
        const isPaused = state === 'PAUSED';
        const isTransitioning = state.startsWith('SUBMITTING_')
            || state === 'WAITING_START_RESULT'
            || state === 'STARTING';
        this.streamIdInput.disabled = !isIdle;
        this.masterUrlInput.disabled = !isIdle;
        if (this.freezeEnabledInput) {
            this.freezeEnabledInput.disabled = !isIdle;
        }
        this.startBtn.disabled = !isIdle;
        this.pauseBtn.disabled = !isRunning;
        this.resumeBtn.disabled = !isPaused;
        this.stopBtn.disabled = !(
            isRunning || isPaused || isTransitioning || state === 'FAILED'
        );
        this.startBtn.textContent = isTransitioning ? 'Starting...' : 'Connect';
    }

    setSystemStatus(statusText, color = null) {
        if (!this.statStatus) return;
        this.statStatus.textContent = statusText || 'UNKNOWN';
        this.statStatus.style.color = color || (
            statusText === 'RUNNING'
                ? 'var(--success)'
                : 'var(--text-secondary)'
        );
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

    setRuntimeTelemetry(status) {
        if (!status) return;
        if (this.statAdmissionMode) {
            this.statAdmissionMode.textContent = (
                status.admission_mode || 'coverage'
            ).toUpperCase();
        }
        if (this.statQueueLag) {
            const lag = status.queue_lag_seconds;
            this.statQueueLag.textContent = Number.isFinite(lag)
                ? `${lag.toFixed(1)} s`
                : '--';
        }
        if (this.statCoverageGaps) {
            this.statCoverageGaps.textContent = String(
                status.coverage_gap_count || 0
            );
        }
        if (this.statDroppedSegments) {
            this.statDroppedSegments.textContent = String(
                status.dropped_media_segment_count || 0
            );
        }
        if (this.statMediaProcesses) {
            this.statMediaProcesses.textContent = (
                `${status.active_media_processes || 0} / `
                + `${status.max_media_processes || 0}`
            );
        }
    }

    resetTelemetry() {
        this.setSystemStatus('IDLE');
        this.setTelemetry({ resolution: '--', fps: '--', bitrate: '--' });
        this.setRuntimeTelemetry({
            admission_mode: 'coverage',
            queue_lag_seconds: null,
            coverage_gap_count: 0,
            dropped_media_segment_count: 0,
            active_media_processes: 0,
            max_media_processes: 0,
        });
        if (this.audioLevelBar) this.audioLevelBar.style.height = '0%';
    }

    addSystemLog(text, level = 'info') {
        const div = document.createElement('div');
        div.className = `log-entry ${level === 'error'
            ? 'error'
            : (level === 'warning' ? 'warning' : 'system-msg')}`;
        const timeSpan = document.createElement('span');
        timeSpan.className = 'log-time';
        timeSpan.textContent = `[${new Date().toLocaleTimeString(
            'vi-VN', { hour12: false }
        )}]`;
        const message = document.createElement('span');
        message.textContent = text;
        div.append(timeSpan, message);
        this._appendLog(div);
    }

    addAlertLog(alert) {
        const presentation = presentAlert(alert);
        const div = document.createElement('div');
        div.className = `log-entry ${presentation.visualClass}`;
        div.dataset.alertId = alert.alert_id || '';
        div.dataset.eventId = alert.event_id || '';
        const timeSpan = document.createElement('span');
        timeSpan.className = 'log-time';
        const occurredAt = alert.occurred_at
            ? new Date(alert.occurred_at)
            : new Date();
        timeSpan.textContent = `[${occurredAt.toLocaleTimeString(
            'vi-VN', { hour12: false }
        )}]`;
        const stateBadge = document.createElement('span');
        stateBadge.className = (
            `badge badge-${presentation.lifecycleLabel.toLowerCase()}`
        );
        stateBadge.textContent = presentation.lifecycleLabel;
        const typeBadge = document.createElement('span');
        typeBadge.className = 'badge badge-type';
        typeBadge.textContent = presentation.eventType;
        div.append(timeSpan, stateBadge, typeBadge);
        if (presentation.variantLabel) {
            const variantBadge = document.createElement('span');
            variantBadge.className = 'badge badge-variant';
            variantBadge.textContent = presentation.variantLabel;
            div.append(variantBadge);
        }
        if (presentation.severity) {
            const severityBadge = document.createElement('span');
            severityBadge.className = (
                `badge badge-${presentation.severity.toLowerCase()}`
            );
            severityBadge.textContent = presentation.severity;
            div.append(severityBadge);
        }
        const reason = document.createElement('span');
        reason.textContent = `${presentation.typeLabel}: ${presentation.reason}`;
        div.append(reason);
        this._appendLog(div);
    }

    _appendLog(element) {
        this.eventLogContainer.append(element);
        this.eventLogContainer.scrollTop = this.eventLogContainer.scrollHeight;
    }
}
